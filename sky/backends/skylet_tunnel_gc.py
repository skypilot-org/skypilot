"""Garbage collection of skylet tunnel processes on this host.

Each host records its skylet tunnels (kubectl port-forward or ssh -L) in the
cluster rows under its own owner id. A tunnel process that matches no pid
this host has recorded is stale, e.g. because another API server sharing the
database tore the cluster down and deleted the row, and is killed here.
"""
import asyncio
import re
import time
from typing import List

import psutil

from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

_GC_INITIAL_DELAY_SECONDS = 60
_GC_INTERVAL_SECONDS = 600
# A tunnel is recorded only after its gRPC channel is ready, so a younger
# process may be a tunnel that is still being opened.
_MIN_TUNNEL_AGE_SECONDS = 300

# kubectl ... port-forward <pod> <local>:<skylet port>
_K8S_FORWARD_ARG = re.compile(rf'\d+:{constants.SKYLET_GRPC_PORT}')
# ssh ... -L <local>:localhost:<skylet port>
_SSH_FORWARD_ARG = re.compile(rf'\d+:localhost:{constants.SKYLET_GRPC_PORT}')


def _is_skylet_tunnel(cmdline: List[str]) -> bool:
    # Tunnels are started through `sh -c`, so the whole command may be a
    # single argv entry.
    tokens = ' '.join(cmdline).split()
    if 'port-forward' in tokens and any(
            _K8S_FORWARD_ARG.fullmatch(t) for t in tokens):
        return True
    return any(_SSH_FORWARD_ARG.fullmatch(t) for t in tokens)


def sweep() -> int:
    """Kills this user's skylet tunnel processes that this host has not
    recorded for any cluster. Returns the number of processes killed.
    """
    recorded_pids = global_user_state.get_skylet_ssh_tunnel_pids(
        backend_utils.skylet_tunnel_owner_id())
    uid = psutil.Process().uids().real
    newest_create_time = time.time() - _MIN_TUNNEL_AGE_SECONDS
    killed = 0
    for proc in psutil.process_iter(['cmdline', 'create_time', 'uids']):
        try:
            info = proc.info
            if (info['uids'] is None or info['uids'].real != uid or
                    not info['cmdline'] or
                    info['create_time'] > newest_create_time or
                    not _is_skylet_tunnel(info['cmdline'])):
                continue
            # The recorded pid is that of the `sh -c` wrapper, which may run
            # the tunnel as its child.
            if proc.pid in recorded_pids or any(
                    parent.pid in recorded_pids for parent in proc.parents()):
                continue
            logger.info(f'Killing unrecorded skylet tunnel process {proc.pid}: '
                        f'{" ".join(info["cmdline"])}')
            subprocess_utils.kill_children_processes(proc.pid)
            killed += 1
        except psutil.Error:
            continue
    return killed


async def gc_daemon() -> None:
    """Runs sweep() periodically for the lifetime of the API server."""
    await asyncio.sleep(_GC_INITIAL_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(sweep)
        except Exception as e:  # pylint: disable=broad-except
            logger.error('Error in skylet tunnel GC: '
                         f'{common_utils.format_exception(e)}')
        await asyncio.sleep(_GC_INTERVAL_SECONDS)
