"""A file-lock based semaphore to limit parallelism of the jobs controller."""

import time
import filelock
import os
from typing import List

class FileLockSemaphore:
    """A cross-process semaphore-like mechanism using file locks.
    
    Some semaphore uses are unsupported:
    - Each release() call must have a corresponding acquire(), that is, the
      semaphore value cannot go above the initial value (lock_count).
    - All processes must use the same lock_count. This is not enforced by the
      FileLockSemaphore class.
    """
    def __init__(self, lock_dir_path: str, lock_count: int):
        self.lock_dir_path = lock_dir_path
        self.locks = [filelock.FileLock(os.path.join(lock_dir_path, f"{i}.lock")) for i in range(lock_count)]
        self.acquired_locks: List[filelock.FileLock] = []

    def acquire(self):
        while True:
            for lock in self.locks:
                try:
                    lock.acquire(blocking=False)
                    self.acquired_locks.append(lock)
                    return
                except filelock.Timeout:
                    pass
            time.sleep(0.05)

    def release(self):
        if self.acquired_locks:
            self.acquired_locks.pop().release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def __del__(self):
        for lock in self.acquired_locks:
            lock.release()
