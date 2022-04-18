import os
import time
from typing import Optional


def log(
    warmup_iter: int = 1,
    stop_iter: Optional[int] = None,
    total_iter: Optional[int] = None,
) -> None:
    pid = os.getpid()
    timestamp = time.time()

    log_path = f'/benchmark-logs/{pid}.log'
    with open(log_path, 'a') as f:
        f.write(f'{timestamp}\n')

    # TODO
