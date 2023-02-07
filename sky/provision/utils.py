import contextlib
import pathlib
import time
from typing import List

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


@contextlib.contextmanager
def check_cache_hash_or_update(cluster_name: str, stage_name: str, hash: str):
    dirname = SKY_CLUSTER_PATH / cluster_name / 'cache'
    dirname.mkdir(parents=True, exist_ok=True)
    path = dirname / stage_name
    if path.exists():
        with open(path) as f:
            updated = f.read() != hash
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
                f.write(hash)
