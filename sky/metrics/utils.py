"""Utilities for processing GPU metrics from Kubernetes clusters."""
import asyncio
import contextlib
import functools
import os
import re
import select
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

import httpx
import prometheus_client as prom

from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils

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

# Default Prometheus deployment that each context's metrics are federated
# from. Overridable via the `metrics.prometheus` server config section.
_DEFAULT_PROMETHEUS_NAMESPACE = 'skypilot'
_DEFAULT_PROMETHEUS_SERVICE = 'skypilot-prometheus-server'
_DEFAULT_PROMETHEUS_SERVICE_PORT = 80

# Path where the pod's service account namespace is mounted.
_SERVICEACCOUNT_NAMESPACE_PATH = (
    '/var/run/secrets/kubernetes.io/serviceaccount/namespace')

# Timeout for the namespace UID probes used by local-context detection.
# Must fit within the per-context timeout budget in sky/server/metrics.py
# (_PER_CONTEXT_TIMEOUT_SECONDS) together with the actual metrics request.
_NAMESPACE_PROBE_TIMEOUT_SECONDS = 5

# TTL for the process-level local-context detection cache. A TTL (instead
# of caching forever) covers the rare case where a kubeconfig context name
# is remapped to a different cluster at runtime; a stale entry self-heals
# within this window. Detection results must NOT live in the request-level
# cache: gpu_metrics() calls annotations.clear_request_level_cache() on
# every scrape, which would turn the probe into per-scrape overhead.
_LOCAL_CONTEXT_CACHE_TTL_SECONDS = 60 * 60

# Process-level cache: context name -> (is_local, detection timestamp).
_local_context_cache: Dict[str, Tuple[bool, float]] = {}
_local_context_cache_lock = threading.Lock()

# (namespace name, namespace UID) of the namespace this API server runs
# in. Only successful reads are cached; failures are retried on the next
# detection attempt.
_own_namespace_identity: Optional[Tuple[str, str]] = None
_own_namespace_identity_lock = threading.Lock()

# Latency buckets shared by histograms that observe seconds. Kept compact to
# bound time-series cardinality (each labeled series multiplies by len(buckets))
# while preserving the 1000s upper bound for slow-call precision.
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
                    60, 120, 300, 600, 1000, float('inf'))

# Time spent processing a piece of code, refer to time_it().
SKY_APISERVER_CODE_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_code_duration_seconds',
    'Time spent processing code',
    ['name', 'group'],
    buckets=_LATENCY_BUCKETS,
)

# Total number of API server requests, grouped by path, method, and status.
# TODO(kevinzwang): Panels that only need method/status grouping should migrate
# to SKY_APISERVER_REQUESTS_BY_USER_TOTAL (aggregated across users). Remove
# this metric after v0.14.0 if all consumers have migrated.
SKY_APISERVER_REQUESTS_TOTAL = prom.Counter(
    'sky_apiserver_requests_total',
    'Total number of API server requests',
    ['path', 'method', 'status'],
)

# Total number of API server requests per user.
# This is a separate metric to avoid high cardinality in the primary metric.
SKY_APISERVER_REQUESTS_BY_USER_TOTAL = prom.Counter(
    'sky_apiserver_requests_by_user_total',
    'Total number of API server requests per user',
    ['user', 'method', 'status'],
)

# Time spent processing API server requests, grouped by path, method, and
# status.
SKY_APISERVER_REQUEST_DURATION_SECONDS = prom.Histogram(
    'sky_apiserver_request_duration_seconds',
    'Time spent processing API server requests',
    ['path', 'method', 'status'],
    buckets=_LATENCY_BUCKETS,
)

# Aggregated across all worker processes — the prometheus_client multiprocess
# collector sums per-process histograms automatically. For per-process
# visibility, see SKY_APISERVER_EVENT_LOOP_LAG_MAX_SECONDS below.
SKY_APISERVER_EVENT_LOOP_LAG_SECONDS = prom.Histogram(
    'sky_apiserver_event_loop_lag_seconds',
    'Scheduling delay of the server event loop',
    buckets=_LATENCY_BUCKETS,
)

# Per-process peak event loop lag observed in the most recent 30s tumbling
# window. Kept as a low-cardinality companion to the (pid-less) lag histogram
# so operators can still attribute spikes to a specific worker.
SKY_APISERVER_EVENT_LOOP_LAG_MAX_SECONDS = prom.Gauge(
    'sky_apiserver_event_loop_lag_max_seconds',
    'Peak event loop lag in the last 30 seconds for each process',
    ['pid'],
    multiprocess_mode='liveall',
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
    buckets=_LATENCY_BUCKETS,
)

SKY_APISERVER_LONG_EXECUTORS = prom.Gauge(
    'sky_apiserver_long_executors',
    'Total number of long-running request executors in the API server',
)

SKY_APISERVER_SHORT_EXECUTORS = prom.Gauge(
    'sky_apiserver_short_executors',
    'Total number of short-running request executors in the API server',
)

# Time a request spends waiting in the task queue (from creation to dequeue).
SKY_APISERVER_QUEUE_WAIT_SECONDS = prom.Histogram(
    'sky_apiserver_queue_wait_seconds',
    'Time a request spent waiting in the task queue before execution',
    ['schedule_type'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
             120.0, 300.0, 600.0, float('inf')),
)

# --- Managed Jobs Metrics ---

# Per-controller-process gauges (consolidation mode only).
# These are updated in ControllerManager.monitor_loop().
SKY_MANAGED_JOBS_CONTROLLER_STARTING_COUNT = prom.Gauge(
    'sky_managed_jobs_controller_starting_count',
    'Number of jobs currently launching on this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

SKY_MANAGED_JOBS_CONTROLLER_RUNNING_COUNT = prom.Gauge(
    'sky_managed_jobs_controller_running_count',
    'Number of running job tasks on this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

SKY_MANAGED_JOBS_CONTROLLER_MAX_JOBS = prom.Gauge(
    'sky_managed_jobs_controller_max_jobs',
    'Computed max jobs for this controller process',
    ['pid'],
    multiprocess_mode='liveall',
)

# Static limit gauge, set in ControllerManager.monitor_loop() alongside
# other per-controller metrics so it stays current if config hot-reload
# is supported in the future.
# Uses pid label + liveall so only controller processes that explicitly call
# .labels(pid=...).set() produce a value, avoiding phantom 0.0 entries from
# API server worker processes that merely import this module.
SKY_MANAGED_JOBS_LIMIT_LAUNCHES_PER_WORKER = prom.Gauge(
    'sky_managed_jobs_limit_launches_per_worker',
    'Max concurrent launches per worker',
    ['pid'],
    multiprocess_mode='liveall',
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


def _get_prometheus_target() -> Tuple[str, str, int]:
    """(namespace, service, port) of the Prometheus to federate from.

    Reads the `metrics.prometheus` server config section, falling back to
    the defaults that match the SkyPilot Helm chart. gpu_metrics() reloads
    the config on every scrape, so changes are picked up at runtime.
    """
    namespace = skypilot_config.get_nested(
        ('metrics', 'prometheus', 'namespace'), _DEFAULT_PROMETHEUS_NAMESPACE)
    service = skypilot_config.get_nested(('metrics', 'prometheus', 'service'),
                                         _DEFAULT_PROMETHEUS_SERVICE)
    port = skypilot_config.get_nested(('metrics', 'prometheus', 'port'),
                                      _DEFAULT_PROMETHEUS_SERVICE_PORT)
    return namespace, service, port


def _get_own_namespace_name() -> Optional[str]:
    """Name of the namespace the API server pod runs in, if any.

    Prefers the POD_NAMESPACE downward-API env var, then falls back to the
    service account namespace file. Returns None when not running in a
    Kubernetes pod.
    """
    namespace = os.environ.get('POD_NAMESPACE')
    if namespace:
        return namespace
    try:
        with open(_SERVICEACCOUNT_NAMESPACE_PATH, encoding='utf-8') as f:
            namespace = f.read().strip()
    except OSError:
        return None
    return namespace or None


def _get_own_namespace_identity() -> Optional[Tuple[str, str]]:
    """(name, UID) of the namespace the API server runs in.

    The UID is read through the in-cluster credentials and acts as the
    identity anchor for local-context detection: a kubeconfig context
    that resolves the same namespace name to the same UID points at the
    cluster this API server runs in.

    Reading the pod's own namespace (rather than e.g. kube-system) is
    deliberate: it only needs a namespaced Role granting `get` on
    `namespaces` inside the release namespace, which works on
    multi-tenant clusters where users cannot get ClusterRoles.

    Returns None when not running in a pod or when the namespace cannot
    be read (e.g. missing RBAC); in that case detection is disabled and
    every context is treated as remote.
    """
    global _own_namespace_identity
    with _own_namespace_identity_lock:
        if _own_namespace_identity is not None:
            return _own_namespace_identity
    namespace = _get_own_namespace_name()
    if namespace is None:
        return None
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    try:
        core = kubernetes_adaptors.core_api(
            kubernetes_adaptors.in_cluster_context_name())
        own_namespace = core.read_namespace(
            namespace, _request_timeout=_NAMESPACE_PROBE_TIMEOUT_SECONDS)
        uid = own_namespace.metadata.uid
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            f'Failed to read own namespace {namespace!r} through in-cluster '
            f'credentials; local-context detection is disabled until the '
            f'next attempt and only the in-cluster context will be treated '
            f'as local: {common_utils.format_exception(e)}')
        return None
    if not uid:
        return None
    with _own_namespace_identity_lock:
        _own_namespace_identity = (namespace, uid)
    return _own_namespace_identity


def _detect_local_context(context: str) -> bool:
    """Probes whether `context` points at the cluster we are running in.

    Reads the API server's own namespace through the context's credentials
    and compares UIDs. Decision table:
      - UID matches -> local.
      - UID differs -> remote.
      - 404 -> remote (namespace does not exist there; clean negative).
      - 403 / timeout / other errors -> assume remote and log a warning.
        Misclassifying local-as-remote degrades to the previous upstream
        behavior for that context; it never corrupts data (stamping is
        idempotent, see add_cluster_name_label). The local cluster can
        still be referenced through the in-cluster context, which is
        always treated as local (see is_local_context).
    """
    identity = _get_own_namespace_identity()
    if identity is None:
        return False
    namespace, own_uid = identity
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    try:
        core = kubernetes_adaptors.core_api(context)
        probed_namespace = core.read_namespace(
            namespace, _request_timeout=_NAMESPACE_PROBE_TIMEOUT_SECONDS)
    except kubernetes_adaptors.api_exception() as e:
        status = getattr(e, 'status', None)
        if status == 404:
            return False
        logger.warning(
            f'Failed to probe namespace {namespace!r} through context '
            f'{context!r} (status={status}); assuming the context is remote: '
            f'{common_utils.format_exception(e)}')
        return False
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            f'Failed to probe namespace {namespace!r} through context '
            f'{context!r}; assuming the context is remote: '
            f'{common_utils.format_exception(e)}')
        return False
    return bool(
        probed_namespace.metadata.uid) and (probed_namespace.metadata.uid
                                            == own_uid)


def is_local_context(context: str) -> bool:
    """Whether a kubeconfig context points at the cluster we run in.

    The in-cluster context is local by construction (its credentials are
    the pod's own service account), so it is treated as local without any
    probing. For named contexts, UID detection is used and its result is
    cached at process level per context name (see
    _LOCAL_CONTEXT_CACHE_TTL_SECONDS). When detection is unavailable or
    fails, named contexts degrade to remote and only the in-cluster
    context keeps being treated as local — i.e. the fallback for a broken
    self-detection is to reference the local cluster via the in-cluster
    context.
    """
    # Import lazily to avoid circular import (metrics -> provision ->
    # clouds -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    if context == kubernetes_adaptors.in_cluster_context_name():
        return True
    now = time.time()
    with _local_context_cache_lock:
        cached = _local_context_cache.get(context)
        if (cached is not None and
                now - cached[1] < _LOCAL_CONTEXT_CACHE_TTL_SECONDS):
            return cached[0]
    is_local = _detect_local_context(context)
    with _local_context_cache_lock:
        _local_context_cache[context] = (is_local, time.time())
    return is_local


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
    # Must be well under the per-context timeout in
    # metrics.py (_PER_CONTEXT_TIMEOUT_SECONDS) to leave
    # time for the HTTP request and cleanup.
    start_port_forward_timeout = 5
    terminate_port_forward_timeout = 5  # 5 second timeout

    # Use ':service_port' to let kubectl choose the local port
    cmd = [
        'kubectl', '--context', context, '-n', namespace, 'port-forward',
        f'service/{service}', f':{service_port}'
    ]

    env = os.environ.copy()
    # Use SkyPilot's kubeconfig discovery which respects KUBECONFIG env var
    # (set by credential manager plugin) and falls back to ~/.kube/config.
    # Always set explicitly so subprocess gets the resolved paths even if
    # env var was modified after os.environ was last copied.
    # Import lazily to avoid circular import (metrics -> provision -> clouds
    # -> metrics).
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import kubernetes as kubernetes_adaptors
    from sky.provision.kubernetes import utils as kubernetes_utils
    kubeconfig_paths = kubernetes_utils.get_kubeconfig_paths()
    env['KUBECONFIG'] = kubernetes_adaptors.ENV_KUBECONFIG_PATH_SEPARATOR.join(
        kubeconfig_paths)

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
        port_forward_process, local_port = await asyncio.to_thread(
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
        # Clean up port forward synchronously to guarantee cleanup
        # even if the task is cancelled by asyncio.wait_for().
        # Using await here would risk CancelledError preventing
        # cleanup.
        if port_forward_process:
            stop_svc_port_forward(port_forward_process)


async def send_local_metrics_request(namespace: str,
                                     service: str,
                                     service_port: int,
                                     endpoint_path: str = '/federate',
                                     match_patterns: Optional[List[str]] = None,
                                     timeout: float = 30.0) -> str:
    """Sends a metrics request to a Prometheus Service in the local cluster.

    Used instead of send_metrics_request_with_port_forward() when the
    context points at the cluster the API server runs in: the Service is
    reachable directly over in-cluster DNS, so no `kubectl port-forward`
    subprocess is needed.

    Args:
        namespace: Namespace where the service is located
        service: Service name to request
        service_port: Port on the service
        endpoint_path: Path to append to the service endpoint (e.g.,
            '/federate')
        match_patterns: List of metric patterns to match (for federate
            endpoint)
        timeout: Request timeout in seconds
    Returns:
        Response text containing the metrics
    """
    endpoint = (f'http://{service}.{namespace}.svc:{service_port}'
                f'{endpoint_path}')
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if match_patterns:
                params = [('match[]', pattern) for pattern in match_patterns]
                response = await client.get(endpoint, params=params)
            else:
                response = await client.get(endpoint)
            response.raise_for_status()
            return response.text
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(f'Failed to send local metrics request to {endpoint}: '
                     f'{common_utils.format_exception(e)}')
        raise


def _add_empty_cluster_matcher(pattern: str) -> str:
    """Restricts a federate match[] selector to never-stamped series.

    Adds `cluster=""` to the selector. This is what makes stamping plus
    re-ingestion loop-free when the API server federates from the same
    Prometheus that scrapes /gpu-metrics: stamped copies carry
    `cluster!=""` and are excluded from the next federation round.
    """
    stripped = pattern.strip()
    if stripped.endswith('}'):
        head = stripped[:-1].rstrip()
        if head.endswith('{'):
            return head + 'cluster=""}'
        return head + ',cluster=""}'
    # Bare metric name without a selector.
    return stripped + '{cluster=""}'


# Matches a `cluster="..."` label inside the label section of an
# exposition-format metric line, at the start or right after a comma.
# Label values may contain escaped quotes (\").
_CLUSTER_LABEL_RE = re.compile(r'(^|,)cluster="(?:\\.|[^"\\])*"')


def _escape_label_value(value: str) -> str:
    """Escapes a string for use as an exposition-format label value.

    Per the Prometheus text format, backslash, double-quote, and newline
    must be escaped inside quoted label values. An unescaped context name
    containing any of these would produce malformed exposition and fail
    the entire scrape.
    """
    return (value.replace('\\', r'\\').replace('"', r'\"').replace('\n', r'\n'))


async def add_cluster_name_label(metrics_text: str, context: str) -> str:
    """Adds a cluster label to each metric line.

    Idempotent: if a series already carries a `cluster` label (e.g. a
    stamped copy that got re-federated), the label is replaced instead of
    prepending a duplicate. A duplicated label would make the exposition
    malformed and fail the entire scrape of the combined /gpu-metrics
    response.

    Args:
        metrics_text: The text containing the metrics
        context: The cluster name
    """
    cluster_value = _escape_label_value(context)
    lines = metrics_text.strip().split('\n')
    modified_lines = []

    for line in lines:
        # keep comment lines and empty lines as-is
        if line.startswith('#') or not line.strip():
            modified_lines.append(line)
            continue
        # if line is a metric line with labels, add cluster label.
        # Use rfind for the closing brace: label values may legitimately
        # contain '}' (the sample value/timestamp after the label section
        # cannot).
        brace_start = line.find('{')
        brace_end = line.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            metric_name = line[:brace_start]
            existing_labels = line[brace_start + 1:brace_end]
            rest_of_line = line[brace_end + 1:]

            if existing_labels:
                new_labels, num_replaced = _CLUSTER_LABEL_RE.subn(
                    lambda m: f'{m.group(1)}cluster="{cluster_value}"',
                    existing_labels)
                if num_replaced == 0:
                    new_labels = f'cluster="{cluster_value}",{existing_labels}'
            else:
                new_labels = f'cluster="{cluster_value}"'

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
    # Query DCGM, host CPU/memory, kube_pod_labels, and cAdvisor container
    # metrics. The container_* metrics enable per-pod CPU/Memory in the
    # Telemetry section by joining on (pod, namespace) with kube_pod_labels —
    # same join shape the GPU panels use to filter by SkyPilot cluster name.
    match_patterns = [
        '{__name__=~"node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes|DCGM_.*"}',  # pylint: disable=line-too-long
        'kube_pod_labels',
        'node_cpu_seconds_total{mode="idle"}',
        'container_cpu_usage_seconds_total{container!="",container!="POD"}',
        'container_memory_working_set_bytes{container!="",container!="POD"}',
        # GPU allocation metrics — pod requests + node capacity for nvidia/amd
        # GPUs. Enables cluster-wide % allocated computations.
        # NOTE: kube-state-metrics sanitizes resource names by replacing
        # `.` and `/` with `_`, so the label value is `nvidia_com_gpu` (not
        # `nvidia.com/gpu`). Getting this wrong causes the match to return 0
        # series while the scrape still succeeds.
        'kube_pod_container_resource_requests{resource=~"nvidia_com_gpu|amd_com_gpu"}',  # pylint: disable=line-too-long
        'kube_node_status_allocatable{resource=~"nvidia_com_gpu|amd_com_gpu"}',
    ]

    prometheus_namespace, prometheus_service, prometheus_port = (
        _get_prometheus_target())

    # is_local_context() may issue a blocking Kubernetes API probe on
    # cache miss; run it in a thread to keep the event loop responsive.
    if await asyncio.to_thread(is_local_context, context):
        # The context points at the cluster this API server runs in:
        # reach the Prometheus Service directly over in-cluster DNS (no
        # port-forward) and federate only never-stamped series
        # (cluster="") so that stamped copies re-ingested by the local
        # Prometheus are never federated again (no self-federation loop).
        local_match_patterns = [
            _add_empty_cluster_matcher(pattern) for pattern in match_patterns
        ]
        metrics_text = await send_local_metrics_request(
            namespace=prometheus_namespace,
            service=prometheus_service,
            service_port=prometheus_port,
            endpoint_path='/federate',
            match_patterns=local_match_patterns)
    else:
        metrics_text = await send_metrics_request_with_port_forward(
            context=context,
            namespace=prometheus_namespace,
            service=prometheus_service,
            service_port=prometheus_port,
            endpoint_path='/federate',
            match_patterns=match_patterns)

    # add cluster name as a label to each metric line
    metrics_text = await add_cluster_name_label(metrics_text, context)

    return metrics_text
