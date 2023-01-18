import pathlib
import time
from typing import List

import socket
from sky.backends import backend_utils

SKYLET_SERVER_LOCAL_PATH = str(
    (pathlib.Path(__file__).parent.parent / 'skylet' / 'server').resolve())
SKYLET_SERVER_REMOTE_PATH = backend_utils._REMOTE_RUNTIME_FILES_DIR + '/server'


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
