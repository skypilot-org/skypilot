import time
from typing import List

import urllib3


def wait_for_ssh(public_ips: List[str]):
    http = urllib3.PoolManager()
    for ip in public_ips:
        while True:
            try:
                http.request('HEAD', f'http://{ip}:22', timeout=1)
            except urllib3.exceptions.ProtocolError:
                # ProtocolError indicates SSH server is already up.
                break
            except urllib3.exceptions.TimeoutError:
                pass
            except Exception:
                time.sleep(0.5)
