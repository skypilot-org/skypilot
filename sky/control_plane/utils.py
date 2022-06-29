import os
import filelock


class FilelockMonotonicID:

    def __init__(self, lock_path: str):
        # TODO(mraheja): remove pylint disabling when filelock
        # version updated
        # pylint: disable=abstract-class-instantiated
        self._lock = filelock.FileLock(lock_path)
        self._counter_path = lock_path + '.counter'
        with self._lock:
            if not os.path.exists(self._counter_path):
                with open(self._counter_path, 'w') as f:
                    f.write('0')

    def next_id(self) -> int:
        with self._lock:
            with open(self._counter_path, 'r+') as f:
                r = int(f.read()) + 1
                f.seek(0)
                f.write(str(r))
                f.truncate()
                return r

    def reset(self):
        with self._lock:
            with open(self._counter_path, 'r+') as f:
                f.seek(0)
                f.write('0')
                f.truncate()
