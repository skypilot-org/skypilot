"""Rich status spinner utils."""
import contextlib
import threading

import rich.console as rich_console

console = rich_console.Console()
_status = None


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


def safe_rich_status(msg: str):
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


def force_update_rich_status(msg: str):
    """Update the status message even if sky_logging.is_silent() is true."""
    if threading.current_thread() is threading.main_thread():
        if _status is not None:
            _status.update(msg)


@contextlib.contextmanager
def rich_safe_logger():
    if threading.current_thread() is threading.main_thread(
    ) and _status is not None:
        _status.stop()
        yield
        _status.start()
    else:
        yield
