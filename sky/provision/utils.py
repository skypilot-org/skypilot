import time
from typing import List

import socket


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
