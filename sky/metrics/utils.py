"""Utilities for processing GPU metrics from Kubernetes clusters."""
import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

import httpx


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

    # start the port forward process
    port_forward_process = subprocess.Popen(cmd,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            text=True,
                                            env=env)

    local_port = None
    start_time = time.time()

    # wait for the port forward to start and extract the local port
    while time.time() - start_time < start_port_forward_timeout:
        if port_forward_process.poll() is not None:
            # port forward process has terminated
            if port_forward_process.returncode != 0:
                raise RuntimeError(
                    f'Port forward failed for service {service} in namespace '
                    f'{namespace} on context {context}')
            break

        # read output line by line to find the local port
        if port_forward_process.stdout:
            line = port_forward_process.stdout.readline()
            if line:
                # look for 'Forwarding from 127.0.0.1:XXXXX -> service_port'
                match = re.search(r'Forwarding from 127\.0\.0\.1:(\d+)', line)
                if match:
                    local_port = int(match.group(1))
                    break

        # sleep for 100ms to avoid busy-waiting
        time.sleep(0.1)

    if local_port is None:
        try:
            port_forward_process.terminate()
            port_forward_process.wait(timeout=terminate_port_forward_timeout)
        except subprocess.TimeoutExpired:
            port_forward_process.kill()
            port_forward_process.wait()
        finally:
            raise RuntimeError(
                f'Failed to extract local port for service {service} in '
                f'namespace {namespace} on context {context}')

    return port_forward_process, local_port


def stop_svc_port_forward(port_forward_process: subprocess.Popen) -> None:
    """Stops a port forward to a service in a Kubernetes cluster.
    Args:
        port_forward_process: The subprocess.Popen process to terminate
    """
    try:
        port_forward_process.terminate()
        port_forward_process.wait(timeout=5)
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
        port_forward_process, local_port = start_svc_port_forward(
            context, namespace, service, service_port)

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

    finally:
        # Always clean up port forward
        if port_forward_process:
            stop_svc_port_forward(port_forward_process)


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
    match_patterns = ['{__name__=~"DCGM_.*"}', 'kube_pod_labels']

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
