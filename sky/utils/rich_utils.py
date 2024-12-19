"""Rich status spinner utils."""
import contextlib
import threading
from typing import Union, Dict, Optional

import rich.console as rich_console

console = rich_console.Console(soft_wrap=True)
_status = None
_status_nesting_level = 0
_main_message = None

_logging_lock = threading.RLock()

# Track sub thread progress statuses
_thread_statuses: Dict[int, Optional[str]] = {}
_status_lock = threading.RLock()

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


class _RevertibleStatus:
    """A wrapper for status that can revert to previous message after exit."""

    def __init__(self, message: str):
        if _main_message is not None:
            self.previous_message = _main_message
        else:
            self.previous_message = None
        self.message = message

    def __enter__(self):
        global _status_nesting_level
        global _main_message
        _main_message = self.message
        refresh()
        _status_nesting_level += 1
        _status.__enter__()
        return _status

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _status_nesting_level, _status
        _status_nesting_level -= 1
        if _status_nesting_level <= 0:
            _status_nesting_level = 0
            if _status is not None:
                _status.__exit__(exc_type, exc_val, exc_tb)
                _status = None
        else:
            global _main_message
            _main_message = self.previous_message
            refresh()

    def update(self, *args, **kwargs):
        _status.update(*args, **kwargs)
        global _main_message
        _main_message = _status.status
        refresh()

    def stop(self):
        _status.stop()

    def start(self):
        _status.start()


class _ThreadStatus:
    """A wrapper of sub thread status"""
    def __init__(self, message: str):
        self.thread_id = threading.get_ident()
        self.message = message
        self.previous_message = _thread_statuses.get(self.thread_id)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.previous_message is not None:
            _thread_statuses[self.thread_id] = self.previous_message
        else:
            # No previous message, remove the thread status
            if self.thread_id in _thread_statuses:
                del _thread_statuses[self.thread_id]
        refresh()

    def update(self, new_message: str):
        self.message = new_message
        _thread_statuses[self.thread_id] = new_message
        refresh()

    def stop(self):
        _thread_statuses[self.thread_id] = None
        refresh()

    def start(self):
        _thread_statuses[self.thread_id] = self.message
        refresh()

def refresh():
    """Refresh status to include all thread statuses."""
    if _status is None or _main_message is None:
        return
    with _status_lock:
        msg = _main_message
        for v in _thread_statuses.values():
            if v is not None:
                msg = msg + f'\n  └─ {v}'
        _status.update(msg)

def safe_status(msg: str) -> Union['rich_console.Status', '_NoOpConsoleStatus']:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    global _status
    if sky_logging.is_silent():
        return _NoOpConsoleStatus()
    if threading.current_thread() is threading.main_thread():
        if _status is None:
            _status = console.status(msg, refresh_per_second=8)
        return _RevertibleStatus(msg)
    else:
        return _ThreadStatus(msg)

def stop_safe_status():
    """Stops all nested statuses.

    This is useful when we need to stop all statuses, e.g., when we are going to
    stream logs from user program and do not want it to interfere with the
    spinner display.
    """
    if (threading.current_thread() is threading.main_thread() and
            _status is not None):
        _status.stop()


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
