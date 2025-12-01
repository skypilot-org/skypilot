"""Utilities for processing GPU metrics from Kubernetes clusters."""
import contextlib
import functools
import os
import re
import select
import subprocess
import time
from typing import List, Optional, Tuple

import httpx
import prometheus_client as prom

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import context_utils

_SELECT_TIMEOUT = 1
_SELECT_BUFFER_SIZE = 4096

_KB = 2**10
_MB = 2**20
_MEM_BUCKETS = [
    _KB,
    256 * _KB,
    512 * _KB,
    _MB,
    2 * _MB,
    4 * _MB,
    8 * _MB,
    16 * _MB,
    32 * _MB,
    64 * _MB,
    128 * _MB,
    256 * _MB,
    float('inf'),
]

logger = sky_logging.init_logger(__name__)

# Whether the metrics are enabled, cannot be changed at runtime.
METRICS_ENABLED = os.environ.get(constants.ENV_VAR_SERVER_METRICS_ENABLED,
                                 'false').lower() == 'true'

# Time spent processing a piece of code, refer to time_it().
SKY_APISERVER_CODE_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_code_duration_seconds',
    'Time spent processing code',
    ['name', 'group'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.25,
             0.35, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 2.75, 3, 3.5, 4, 4.5,
             5, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
             50.0, 55.0, 60.0, 80.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0,
             240.0, 260.0, 280.0, 300.0, 320.0, 340.0, 360.0, 380.0, 400.0,
             420.0, 440.0, 460.0, 480.0, 500.0, 520.0, 540.0, 560.0, 580.0,
             600.0, 620.0, 640.0, 660.0, 680.0, 700.0, 720.0, 740.0, 760.0,
             780.0, 800.0, 820.0, 840.0, 860.0, 880.0, 900.0, 920.0, 940.0,
             960.0, 980.0, 1000.0, float('inf')),
)

# Total number of API server requests, grouped by path, method, and status.
SKY_APISERVER_REQUESTS_TOTAL = prom.Counter(
    'sky_apiserver_requests_total',
    'Total number of API server requests',
    ['path', 'method', 'status'],
)

# Time spent processing API server requests, grouped by path, method, and
# status.
SKY_APISERVER_REQUEST_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_request_duration_seconds',
    'Time spent processing API server requests',
    ['path', 'method', 'status'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.25,
             0.35, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 2.75, 3, 3.5, 4, 4.5,
             5, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
             50.0, 55.0, 60.0, 80.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0,
             240.0, 260.0, 280.0, 300.0, 320.0, 340.0, 360.0, 380.0, 400.0,
             420.0, 440.0, 460.0, 480.0, 500.0, 520.0, 540.0, 560.0, 580.0,
             600.0, 620.0, 640.0, 660.0, 680.0, 700.0, 720.0, 740.0, 760.0,
             780.0, 800.0, 820.0, 840.0, 860.0, 880.0, 900.0, 920.0, 940.0,
             960.0, 980.0, 1000.0, float('inf')),
)

SKY_APISERVER_EVENT_LOOP_LAG_SECONDS = prom.Histogram(
    'sky_apiserver_event_loop_lag_seconds',
    'Scheduling delay of the server event loop',
    ['pid'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.25,
             0.35, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 2.75, 3, 3.5, 4, 4.5,
             5, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
             50.0, 55.0, 60.0, 80.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0,
             240.0, 260.0, 280.0, 300.0, 320.0, 340.0, 360.0, 380.0, 400.0,
             420.0, 440.0, 460.0, 480.0, 500.0, 520.0, 540.0, 560.0, 580.0,
             600.0, 620.0, 640.0, 660.0, 680.0, 700.0, 720.0, 740.0, 760.0,
             780.0, 800.0, 820.0, 840.0, 860.0, 880.0, 900.0, 920.0, 940.0,
             960.0, 980.0, 1000.0, float('inf')),
)

SKY_APISERVER_WEBSOCKET_CONNECTIONS = prom.Gauge(
    'sky_apiserver_websocket_connections',
    'Number of websocket connections',
    ['pid'],
    multiprocess_mode='livesum',
)

SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL = prom.Counter(
    'sky_apiserver_websocket_closed_total',
    'Number of websocket closed',
    ['pid', 'reason'],
)

# The number of execution starts in each worker process, we do not record
# histogram here as the duration has been measured in
# SKY_APISERVER_CODE_DURATION_SECONDS without the worker label (process id).
# Recording histogram WITH worker label will cause high cardinality.
SKY_APISERVER_PROCESS_EXECUTION_START_TOTAL = prom.Counter(
    'sky_apiserver_process_execution_start_total',
    'Total number of execution starts in each worker process',
    ['request', 'pid'],
)

SKY_APISERVER_PROCESS_PEAK_RSS = prom.Gauge(
    'sky_apiserver_process_peak_rss',
    'Peak RSS we saw in each process in last 30 seconds',
    ['pid', 'type'],
)

SKY_APISERVER_PROCESS_CPU_TOTAL = prom.Gauge(
    'sky_apiserver_process_cpu_total',
    'Total CPU times a worker process has been running',
    ['pid', 'type', 'mode'],
)

SKY_APISERVER_REQUEST_MEMORY_USAGE_BYTES = prom.Histogram(
    'sky_apiserver_request_memory_usage_bytes',
    'Peak memory usage of requests', ['name'],
    buckets=_MEM_BUCKETS)

SKY_APISERVER_REQUEST_RSS_INCR_BYTES = prom.Histogram(
    'sky_apiserver_request_rss_incr_bytes',
    'RSS increment after requests', ['name'],
    buckets=_MEM_BUCKETS)

SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS = prom.Histogram(
    'sky_apiserver_websocket_ssh_latency_seconds',
    ('Time taken for ssh message to go from client to API server and back'
     'to the client. This does not include: latency to reach the pod, '
     'overhead from sending through the k8s port-forward tunnel, or '
     'ssh server lag on the destination pod.'),
    ['pid'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.25,
             0.35, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 2.75, 3, 3.5, 4, 4.5,
             5, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
             50.0, 55.0, 60.0, 80.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0,
             240.0, 260.0, 280.0, 300.0, 320.0, 340.0, 360.0, 380.0, 400.0,
             420.0, 440.0, 460.0, 480.0, 500.0, 520.0, 540.0, 560.0, 580.0,
             600.0, 620.0, 640.0, 660.0, 680.0, 700.0, 720.0, 740.0, 760.0,
             780.0, 800.0, 820.0, 840.0, 860.0, 880.0, 900.0, 920.0, 940.0,
             960.0, 980.0, 1000.0, float('inf')),
)

SKY_APISERVER_LONG_EXECUTORS = prom.Gauge(
    'sky_apiserver_long_executors',
    'Total number of long-running request executors in the API server',
)

SKY_APISERVER_SHORT_EXECUTORS = prom.Gauge(
    'sky_apiserver_short_executors',
    'Total number of short-running request executors in the API server',
)


@contextlib.contextmanager
def time_it(name: str, group: str = 'default'):
    """Context manager to measure and record code execution duration."""
    if not METRICS_ENABLED:
        yield
    else:
        start_time = time.time()
        try:
            yield
        finally:
            duration = time.time() - start_time
            SKY_APISERVER_CODE_DURATION_SECONDS.labels(
                name=name, group=group).observe(duration)


def time_me(func):
    """Measure the duration of decorated function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not METRICS_ENABLED:
            return func(*args, **kwargs)
        name = f'{func.__module__}/{func.__name__}'
        with time_it(name, group='function'):
            return func(*args, **kwargs)

    return wrapper


def time_me_async(func):
    """Measure the duration of decorated async function."""

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        if not METRICS_ENABLED:
            return await func(*args, **kwargs)
        name = f'{func.__module__}/{func.__name__}'
        with time_it(name, group='function'):
            return await func(*args, **kwargs)

    return async_wrapper


def start_svc_port_forward(context: str, namespace: str, service: str,
                           service_port: int) -> Tuple[subprocess.Popen, int]:
    """Starts a port forward to a service in a Kubernetes cluster.
    Args:
        context: Kubernetes context name
        namespace: Namespace where the service is located
        service: Service name to port forward to
        service_port: Port on the service to forward to
    Returns:
        Tuple of (subprocess.Popen process, local_port assigned)
    Raises:
        RuntimeError: If port forward fails to start
    """
    start_port_forward_timeout = 10  # 10 second timeout
    terminate_port_forward_timeout = 5  # 5 second timeout

    # Use ':service_port' to let kubectl choose the local port
    cmd = [
        'kubectl', '--context', context, '-n', namespace, 'port-forward',
        f'service/{service}', f':{service_port}'
    ]

    env = os.environ.copy()
    if 'KUBECONFIG' not in env:
        env['KUBECONFIG'] = os.path.expanduser('~/.kube/config')

    port_forward_process = None
    port_forward_exit = False
    local_port = None
    poller = None
    fd = None

    try:
        # start the port forward process
        port_forward_process = subprocess.Popen(cmd,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT,
                                                text=True,
                                                env=env)

        # Use poll() instead of select() to avoid FD_SETSIZE limit
        poller = select.poll()
        assert port_forward_process.stdout is not None
        fd = port_forward_process.stdout.fileno()
        poller.register(fd, select.POLLIN)

        start_time = time.time()
        buffer = ''
        # wait for the port forward to start and extract the local port
        while time.time() - start_time < start_port_forward_timeout:
            if port_forward_process.poll() is not None:
                # port forward process has terminated
                if port_forward_process.returncode != 0:
                    port_forward_exit = True
                break

            # Wait up to 1000ms for data to be available without blocking
            # poll() takes timeout in milliseconds
            events = poller.poll(_SELECT_TIMEOUT * 1000)

            if events:
                # Read available bytes from the FD without blocking
                raw = os.read(fd, _SELECT_BUFFER_SIZE)
                chunk = raw.decode(errors='ignore')
                buffer += chunk
                match = re.search(r'Forwarding from 127\.0\.0\.1:(\d+)', buffer)
                if match:
                    local_port = int(match.group(1))
                    break

            # sleep for 100ms to avoid busy-waiting
            time.sleep(0.1)
    except BaseException:  # pylint: disable=broad-exception-caught
        if port_forward_process:
            stop_svc_port_forward(port_forward_process,
                                  timeout=terminate_port_forward_timeout)
        raise
    finally:
        if poller is not None and fd is not None:
            try:
                poller.unregister(fd)
            except (OSError, ValueError):
                # FD may already be unregistered or invalid
                pass
    if port_forward_exit:
        raise RuntimeError(f'Port forward failed for service {service} in '
                           f'namespace {namespace} on context {context}')
    if local_port is None:
        try:
            if port_forward_process:
                stop_svc_port_forward(port_forward_process,
                                      timeout=terminate_port_forward_timeout)
        finally:
            raise RuntimeError(
                f'Failed to extract local port for service {service} in '
                f'namespace {namespace} on context {context}')

    return port_forward_process, local_port


def stop_svc_port_forward(port_forward_process: subprocess.Popen,
                          timeout: int = 5) -> None:
    """Stops a port forward to a service in a Kubernetes cluster.
    Args:
        port_forward_process: The subprocess.Popen process to terminate
    """
    try:
        port_forward_process.terminate()
        port_forward_process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        port_forward_process.kill()
        port_forward_process.wait()


async def send_metrics_request_with_port_forward(
        context: str,
        namespace: str,
        service: str,
        service_port: int,
        endpoint_path: str = '/federate',
        match_patterns: Optional[List[str]] = None,
        timeout: float = 30.0) -> str:
    """Sends a metrics request to a Prometheus endpoint via port forwarding.
    Args:
        context: Kubernetes context name
        namespace: Namespace where the service is located
        service: Service name to port forward to
        service_port: Port on the service to forward to
        endpoint_path: Path to append to the localhost endpoint (e.g.,
            '/federate')
        match_patterns: List of metric patterns to match (for federate
            endpoint)
        timeout: Request timeout in seconds
    Returns:
        Response text containing the metrics
    Raises:
        RuntimeError: If port forward or HTTP request fails
    """
    port_forward_process = None
    try:
        # Start port forward
        port_forward_process, local_port = await context_utils.to_thread(
            start_svc_port_forward, context, namespace, service, service_port)

        # Build endpoint URL
        endpoint = f'http://localhost:{local_port}{endpoint_path}'

        # Make HTTP request
        async with httpx.AsyncClient(timeout=timeout) as client:
            if match_patterns:
                # For federate endpoint, add match[] parameters
                params = [('match[]', pattern) for pattern in match_patterns]
                response = await client.get(endpoint, params=params)
            else:
                response = await client.get(endpoint)

            response.raise_for_status()
            return response.text

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(f'Failed to send metrics request with port forward: '
                     f'{common_utils.format_exception(e)}')
        raise
    finally:
        # Always clean up port forward
        if port_forward_process:
            await context_utils.to_thread(stop_svc_port_forward,
                                          port_forward_process)


async def add_cluster_name_label(metrics_text: str, context: str) -> str:
    """Adds a cluster_name label to each metric line.
    Args:
        metrics_text: The text containing the metrics
        context: The cluster name
    """
    lines = metrics_text.strip().split('\n')
    modified_lines = []

    for line in lines:
        # keep comment lines and empty lines as-is
        if line.startswith('#') or not line.strip():
            modified_lines.append(line)
            continue
        # if line is a metric line with labels, add cluster label
        brace_start = line.find('{')
        brace_end = line.find('}')
        if brace_start != -1 and brace_end != -1:
            metric_name = line[:brace_start]
            existing_labels = line[brace_start + 1:brace_end]
            rest_of_line = line[brace_end + 1:]

            if existing_labels:
                new_labels = f'cluster="{context}",{existing_labels}'
            else:
                new_labels = f'cluster="{context}"'

            modified_line = f'{metric_name}{{{new_labels}}}{rest_of_line}'
            modified_lines.append(modified_line)
        else:
            # keep other lines as-is
            modified_lines.append(line)

    return '\n'.join(modified_lines)


async def get_metrics_for_context(context: str) -> str:
    """Get GPU metrics for a single Kubernetes context.
    Args:
        context: Kubernetes context name
    Returns:
        metrics_text: String containing the metrics
    Raises:
        Exception: If metrics collection fails for any reason
    """
    # Query both DCGM metrics and kube_pod_labels metrics
    # This ensures the dashboard can perform joins to filter by skypilot cluster
    match_patterns = [
        '{__name__=~"node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes|DCGM_.*"}',  # pylint: disable=line-too-long
        'kube_pod_labels',
        'node_cpu_seconds_total{mode="idle"}'
    ]

    # TODO(rohan): don't hardcode the namespace and service name
    metrics_text = await send_metrics_request_with_port_forward(
        context=context,
        namespace='skypilot',
        service='skypilot-prometheus-server',
        service_port=80,
        endpoint_path='/federate',
        match_patterns=match_patterns)

    # add cluster name as a label to each metric line
    metrics_text = await add_cluster_name_label(metrics_text, context)

    return metrics_text
