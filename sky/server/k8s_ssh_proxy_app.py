"""SkyPilot API Server exposing RESTful APIs."""

import argparse
import asyncio
import base64
import contextlib
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import multiprocessing
import os
import pathlib
import posixpath
import re
import resource
import shutil
import signal
import sys
import threading
import time
from typing import Dict, List, Literal, Optional, Set, Tuple
import uuid
import zipfile

import importlib
import aiofiles
import anyio
import fastapi
import starlette.middleware.base
# Remove the current directory from sys.path temporarily
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if p != current_dir]

# Import the real pip-installed 'x'
x_pkg = importlib.import_module('uvicorn')

# Restore sys.path
sys.path.insert(0, current_dir)
import uvicorn
import uvloop

import sky
from sky import catalog
from sky import check as sky_check
from sky import core
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.data import storage_utils
from sky.jobs import utils as managed_job_utils
from sky.jobs.server import server as jobs_rest
from sky.metrics import utils as metrics_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.schemas.api import responses
from sky.serve.server import server as serve_rest
from sky.server import common
from sky.server import config as server_config
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server import metrics
from sky.server import middleware as middleware_config
from sky.server import reverse_proxy
from sky.server import state
from sky.server import stream_utils
from sky.server import versions
from sky.server.auth import authn
from sky.server.auth import loopback
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.ssh_node_pools import server as ssh_node_pools_rest
from sky.usage import usage_lib
from sky.users import permission
from sky.users import server as users_rest
from sky.utils import admin_policy_utils
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import context
from sky.utils import context_utils
from sky.utils import dag_utils
from sky.utils import perf_utils
from sky.utils import subprocess_utils
from sky.utils.db import db_utils
from sky.volumes.server import server as volumes_rest
from sky.workspaces import server as workspaces_rest
from sky.utils import context_utils
from sky.utils import status_lib
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

# increase the resource limit for the server
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

# Increase the limit of files we can open to our hard limit. This fixes bugs
# where we can not aquire file locks or open enough logs and the API server
# crashes. On Mac, the hard limit is 9,223,372,036,854,775,807.
# TODO(luca) figure out what to do if we need to open more than 2^63 files.
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
except Exception:  # pylint: disable=broad-except
    pass  # no issue, we will warn the user later if its too low


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

if __name__ == '__main__':

    from sky.server import uvicorn as skyuvicorn

    logger.info('Initializing SkyPilot API server')
    skyuvicorn.add_timestamp_prefix_for_server_logs()

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port',
                        default=46582,
                        type=int,
                        help='Port for the Kubernetes SSH proxy application')
    parser.add_argument('--deploy', action='store_true')
    os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = 'true'
    cmd_args = parser.parse_args()

    usage_lib.maybe_show_privacy_policy()

    # Initialize global user state db
    db_utils.set_max_connections(1)
    logger.info('Initializing database engine')
    global_user_state.get_db_engine()
    logger.info('Database engine initialized')

    max_db_connections = global_user_state.get_max_db_connections()
    logger.info(f'Max db connections: {max_db_connections}')
    config = server_config.compute_server_config(cmd_args.deploy,
                                                 max_db_connections)

    num_workers = config.num_server_workers

    try:

        logger.info(f'Starting SkyPilot K8s SSH proxy server on port '
                    f'{cmd_args.port}, workers={num_workers}')
        # We don't support reload for now, since it may cause leakage of request
        # workers or interrupt running requests.
        uvicorn_config = uvicorn.Config('sky.server.k8s_ssh_proxy_app:app',
                                        host=cmd_args.host,
                                        port=cmd_args.port,
                                        workers=num_workers)
        server = skyuvicorn.Server(config=uvicorn_config)
        server.run()
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(f'Failed to start SkyPilot API server: '
                     f'{common_utils.format_exception(exc, use_bracket=True)}')
        raise
    finally:
        logger.info('Shutting down SkyPilot API server...')
