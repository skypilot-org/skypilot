"""Rich status spinner utils."""
import contextlib
import threading

import rich.console as rich_console
import rich.progress as rich_progress

console = rich_console.Console()
_status = None

_progress = None
_logging_lock = threading.RLock()


class _NoOpConsoleStatus:
    """An empty class for multi-threaded console.status."""

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def update(self, text):
        pass

    def stop(self):
        pass

    def start(self):
        pass

class _NoOpProgress:
    """An empty class for multi-threaded rich.progress."""

    def __enter__(self):
        self.live.transient = False

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def update(self, text):
        pass

    def track(self, iterable, description=None, total=None):
        pass

    def add_task(self, description, total=None, start=False):
        pass

    def stop(self):
        pass

    def start(self):
        pass

def safe_status(msg: str):
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


def safe_progress(transient: bool, redirect_stdout: bool,
                  redirect_stderr: bool):
    """ A wrapper for multi-threaded rich.progress."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _progress
        if _progress is None:
            _progress = rich_progress.Progress(transient=transient,
                                               redirect_stdout=redirect_stdout,
                                               redirect_stderr=redirect_stderr)
        return _progress
    return _NoOpProgress()

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
