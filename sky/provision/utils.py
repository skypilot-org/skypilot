"""Utils for provisioning"""
import contextlib
import functools
import json
import pathlib
import shutil
import subprocess
import time
from typing import Dict, Optional

import socket

from sky import sky_logging
from sky.provision import common as provision_comm
from sky.utils import command_runner

SKY_CLUSTER_PATH = pathlib.Path.home() / '.sky' / 'clusters'
logger = sky_logging.init_logger(__name__)


def _wait_ssh_connection_direct(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 22), timeout=1) as s:
            if s.recv(100).startswith(b'SSH'):
                return True
    except socket.timeout:  # this is the most expected exception
        pass
    except Exception:  # pylint: disable=broad-except
        pass
    return False


def _wait_ssh_connection_indirect(
        ip: str,
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None) -> bool:
    # We test ssh with 'echo', because it is of the most common
    # commandline programs on both Unix-like and Windows platforms.
    command = ['ssh', '-T'] + command_runner.ssh_options_list(
        ssh_private_key,
        ssh_control_name,
        ssh_proxy_command=ssh_proxy_command,
        timeout=1,
    ) + [f'{ssh_user}@{ip}'] + ['echo']
    proc = subprocess.run(command, shell=False, check=False)
    return proc.returncode == 0


def wait_for_ssh(cluster_metadata: provision_comm.ClusterMetadata,
                 ssh_credentials: Dict[str, str]):
    """Wait until SSH is ready."""
    ips = cluster_metadata.get_feasible_ips()
    if cluster_metadata.has_public_ips():
        # If we can access public IPs, then it is more efficient to test SSH
        # connection with raw sockets.
        waiter = _wait_ssh_connection_direct
    else:
        waiter = functools.partial(_wait_ssh_connection_indirect,
                                   **ssh_credentials)
    for ip in ips:
        # Currently we just wait for SSH forever, because we have
        # no idea about when SSH is ready (and it typically takes a
        # long time). Users can simply interrupt
        # the CLI if they feel it is waiting too long.
        while not waiter(ip):
            time.sleep(1)


def cache_func(cluster_name: str, instance_id: str, stage_name: str,
               hash_str: str):
    """A helper function for caching function execution."""

    def decorator(function):

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            with check_cache_hash_or_update(cluster_name, instance_id,
                                            stage_name, hash_str) as updated:
                if updated:
                    return function(*args, **kwargs)
                return None

        return wrapper

    return decorator


@contextlib.contextmanager
def check_cache_hash_or_update(cluster_name: str, instance_id: str,
                               stage_name: str, hash_str: str):
    """A decorator for 'cache_func'."""
    path = get_cache_dir(cluster_name, instance_id) / stage_name
    if path.exists():
        with open(path) as f:
            updated = f.read() != hash_str
    else:
        updated = True
    logger.debug(f'Update {cluster_name}/{instance_id}/{stage_name}: '
                 f'{str(updated)}')
    errored = False
    try:
        yield updated
    except Exception as e:
        errored = True
        raise e
    finally:
        if not errored and (not path.exists() or updated):
            with open(path, 'w') as f:
                f.write(hash_str)


def get_cache_dir(cluster_name: str, instance_id: str) -> pathlib.Path:
    """This function returns a pathlib.Path object representing the cache
    directory for the specified cluster and instance. If the directory
    does not exist, it is created."""
    dirname = (SKY_CLUSTER_PATH / cluster_name / 'instances' / instance_id /
               'cache')
    dirname.mkdir(parents=True, exist_ok=True)
    return dirname.resolve()


def get_log_dir(cluster_name: str, instance_id: str) -> pathlib.Path:
    """This function returns a pathlib.Path object representing the
    log directory for the specified cluster and instance. If the
    directory does not exist, it is created."""
    dirname = (SKY_CLUSTER_PATH / cluster_name / 'instances' / instance_id /
               'logs')
    dirname.mkdir(exist_ok=True, parents=True)
    return dirname.resolve()


def remove_cluster_profile(cluster_name: str) -> None:
    """Remove profiles of a cluster. This is called when terminating
    the cluster."""
    dirname = SKY_CLUSTER_PATH / cluster_name
    logger.debug(f'Remove profile of cluster {cluster_name}.')
    shutil.rmtree(dirname, ignore_errors=True)


def generate_metadata(cloud_name: str, cluster_name: str) -> pathlib.Path:
    """This function generates metadata for the specified cloud and cluster,
    and returns the path to the metadata file. The metadata is a dictionary
    containing the cloud name and the cluster name."""
    metadata = {
        'cloud': cloud_name,
        'cluster_name': cluster_name,
    }
    (SKY_CLUSTER_PATH / cluster_name).mkdir(exist_ok=True, parents=True)
    path = SKY_CLUSTER_PATH / cluster_name / 'metadata.json'
    with open(path, 'w') as f:
        json.dump(metadata, f)
    return path


def get_metadata_in_cluster() -> Optional[Dict]:
    """This function attempts to open the metadata file located at
    ~/.sky/metadata.json and returns its contents as a dictionary.
    If the metadata file does not exist, it returns None."""
    try:
        with open(pathlib.Path.home() / '.sky' / 'metadata.json') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
