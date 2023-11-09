"""Rich status spinner utils."""
import contextlib
import threading
from typing import Union

import rich.console as rich_console

console = rich_console.Console()
_status = None

_logging_lock = threading.RLock()


class _NoOpConsoleStatus:
    """An empty class for multi-threaded console.status."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def update(self, text):
        pass

    def stop(self):
        pass

    def start(self):
        pass


def safe_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _status
        if _status is None:
            _status = console.status(msg)
        _status.update(msg)
        return _status
    return _NoOpConsoleStatus()


def force_update_status(msg: str):
    """Update the status message even if sky_logging.is_silent() is true."""
    if (threading.current_thread() is threading.main_thread() and
            _status is not None):
        _status.update(msg)


@contextlib.contextmanager
def safe_logger():
    logged = False
    with _logging_lock:
        if _status is not None and _status._live.is_started:  # pylint: disable=protected-access
            _status.stop()
            yield
            logged = True
            _status.start()
    if not logged:
        yield
