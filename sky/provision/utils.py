"""Utils for provisioning"""
import contextlib
import functools
import json
import pathlib
import shutil
import time
from typing import List, Dict, Optional

import socket
from sky.backends import backend_utils

SKYLET_SERVER_LOCAL_PATH = str(
    (pathlib.Path(__file__).parent.parent / 'skylet' / 'server').resolve())
SKYLET_SERVER_REMOTE_PATH = backend_utils._REMOTE_RUNTIME_FILES_DIR + '/server'
SKY_CLUSTER_PATH = pathlib.Path.home() / '.sky' / 'clusters'


def wait_for_ssh(public_ips: List[str]):
    for ip in public_ips:
        while True:
            try:
                with socket.create_connection((ip, 22), timeout=1) as s:
                    if s.recv(100).startswith(b'SSH'):
                        break
            except socket.timeout:
                pass
            except Exception:
                time.sleep(1)


def cache_func(cluster_name: str, instance_id: str, stage_name: str,
               hash_str: str):

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
    path = get_cache_dir(cluster_name, instance_id) / stage_name
    if path.exists():
        with open(path) as f:
            updated = f.read() != hash_str
    else:
        updated = True

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
    dirname = (SKY_CLUSTER_PATH / cluster_name / 'instances' / instance_id /
               'cache')
    dirname.mkdir(parents=True, exist_ok=True)
    return dirname.resolve()


def get_log_dir(cluster_name: str, instance_id: str) -> pathlib.Path:
    dirname = (SKY_CLUSTER_PATH / cluster_name / 'instances' / instance_id /
               'logs')
    dirname.mkdir(exist_ok=True, parents=True)
    return dirname.resolve()


def remove_cluster_profile(cluster_name: str) -> None:
    """Remove profiles of a cluster. This is called when terminating
    the cluster.
    """
    dirname = SKY_CLUSTER_PATH / cluster_name
    shutil.rmtree(dirname, ignore_errors=True)


def generate_metadata(cloud_name: str, cluster_name: str) -> pathlib.Path:
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
    try:
        with open(pathlib.Path.home() / '.sky' / 'metadata.json') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
