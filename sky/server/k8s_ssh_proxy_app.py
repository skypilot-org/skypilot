"""Kubernetes SSH Proxy WebSocket application.

This module contains a separate FastAPI application that handles WebSocket
connections for SSH proxy to Kubernetes pods. It's separated from the main
API server to run on its own port for better resource management.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

import fastapi

from sky import clouds
from sky import core
from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.server import middleware as middleware_config
from sky.utils import context_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# Create the FastAPI application for kubernetes SSH proxy
app = fastapi.FastAPI(debug=True)

# Apply all standard middleware
middleware_config.apply_middleware(app)


@app.websocket('/kubernetes-pod-ssh-proxy')
async def kubernetes_pod_ssh_proxy(websocket: fastapi.WebSocket,
                                   cluster_name: str) -> None:
    """Proxies SSH to the Kubernetes pod with websocket."""
    await websocket.accept()
    logger.info(f'WebSocket connection accepted for cluster: {cluster_name}')

    # Run core.status in another thread to avoid blocking the event loop.
    with ThreadPoolExecutor(max_workers=1) as thread_pool_executor:
        cluster_records = await context_utils.to_thread_with_executor(
            thread_pool_executor, core.status, cluster_name, all_users=True)
    cluster_record = cluster_records[0]
    if cluster_record['status'] != status_lib.ClusterStatus.UP:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Cluster {cluster_name} is not running')

    handle = cluster_record['handle']
    assert handle is not None, 'Cluster handle is None'
    if not isinstance(handle.launched_resources.cloud, clouds.Kubernetes):
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cluster {cluster_name} is not a Kubernetes cluster'
            'Use ssh to connect to the cluster instead.')

    kubectl_cmd = handle.get_command_runners()[0].port_forward_command(
        port_forward=[(None, 22)])
    proc = await asyncio.create_subprocess_exec(
        *kubectl_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    logger.info(f'Started kubectl port-forward with command: {kubectl_cmd}')

    # Wait for port-forward to be ready and get the local port
    local_port = None
    assert proc.stdout is not None
    while True:
        stdout_line = await proc.stdout.readline()
        if stdout_line:
            decoded_line = stdout_line.decode()
            logger.info(f'kubectl port-forward stdout: {decoded_line}')
            if 'Forwarding from 127.0.0.1' in decoded_line:
                port_str = decoded_line.split(':')[-1]
                local_port = int(port_str.replace(' -> ', ':').split(':')[0])
                break
        else:
            await websocket.close()
            return

    logger.info(f'Starting port-forward to local port: {local_port}')
    conn_gauge = metrics_utils.SKY_APISERVER_WEBSOCKET_CONNECTIONS.labels(
        pid=os.getpid())
    ssh_failed = False
    websocket_closed = False
    try:
        conn_gauge.inc()
        # Connect to the local port
        reader, writer = await asyncio.open_connection('127.0.0.1', local_port)

        async def websocket_to_ssh():
            try:
                async for message in websocket.iter_bytes():
                    writer.write(message)
                    try:
                        await writer.drain()
                    except Exception as e:  # pylint: disable=broad-except
                        # Typically we will not reach here, if the ssh to pod
                        # is disconnected, ssh_to_websocket will exit first.
                        # But just in case.
                        logger.error('Failed to write to pod through '
                                     f'port-forward connection: {e}')
                        nonlocal ssh_failed
                        ssh_failed = True
                        break
            except fastapi.WebSocketDisconnect:
                pass
            nonlocal websocket_closed
            websocket_closed = True
            writer.close()

        async def ssh_to_websocket():
            try:
                while True:
                    data = await reader.read(1024)
                    if not data:
                        if not websocket_closed:
                            logger.warning('SSH connection to pod is '
                                           'disconnected before websocket '
                                           'connection is closed')
                            nonlocal ssh_failed
                            ssh_failed = True
                        break
                    await websocket.send_bytes(data)
            except Exception:  # pylint: disable=broad-except
                pass
            try:
                await websocket.close()
            except Exception:  # pylint: disable=broad-except
                # The websocket might has been closed by the client.
                pass

        await asyncio.gather(websocket_to_ssh(), ssh_to_websocket())
    finally:
        conn_gauge.dec()
        reason = ''
        try:
            logger.info('Terminating kubectl port-forward process')
            proc.terminate()
        except ProcessLookupError:
            stdout = await proc.stdout.read()
            logger.error('kubectl port-forward was terminated before the '
                         'ssh websocket connection was closed. Remaining '
                         f'output: {str(stdout)}')
            reason = 'KubectlPortForwardExit'
            metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
                pid=os.getpid(), reason='KubectlPortForwardExit').inc()
        else:
            if ssh_failed:
                reason = 'SSHToPodDisconnected'
            else:
                reason = 'ClientClosed'
        metrics_utils.SKY_APISERVER_WEBSOCKET_CLOSED_TOTAL.labels(
            pid=os.getpid(), reason=reason).inc()


@app.get('/health')
async def health() -> dict:
    """Simple health check endpoint for the K8s SSH proxy app."""
    return {'status': 'healthy', 'service': 'k8s-ssh-proxy'}
