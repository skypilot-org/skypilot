"""Rich status spinner utils."""
import contextlib
import enum
import logging
import threading
from typing import Optional, Tuple, Union

import rich.console as rich_console

from sky.utils import message_utils

console = rich_console.Console()
_status = None
_rich_status = None

_logging_lock = threading.RLock()


class Control(enum.Enum):
    """Control codes for the status spinner."""
    INIT = 'rich_init'
    START = 'rich_start'
    STOP = 'rich_stop'
    UPDATE = 'rich_update'

    def encode(self, msg: str) -> str:
        return f'<{self.value}>{msg}</{self.value}>'

    @classmethod
    def decode(cls, encoded_msg: str) -> Tuple[Optional['Control'], str]:
        # Find the control code
        control_str = None
        for control in cls:
            if f'<{control.value}>' in encoded_msg:
                control_str = control.value
                encoded_msg = encoded_msg.replace(f'<{control.value}>', '')
                encoded_msg = encoded_msg.replace(f'</{control.value}>', '')
                break
        else:
            return None, encoded_msg
        return cls(control_str), encoded_msg


class EncodedStatusMessage:
    """A class to encode status messages."""

    def __init__(self, msg: str):
        self.msg = msg

    def init(self) -> str:
        return message_utils.encode_payload(Control.INIT.encode(self.msg))

    def enter(self) -> str:
        return message_utils.encode_payload(Control.START.encode(self.msg))

    def exit(self) -> str:
        return message_utils.encode_payload(Control.STOP.encode(''))

    def update(self, msg: str) -> str:
        return message_utils.encode_payload(Control.UPDATE.encode(msg))

    def stop(self) -> str:
        return message_utils.encode_payload(Control.STOP.encode(''))

    def start(self) -> str:
        return message_utils.encode_payload(Control.START.encode(self.msg))


class EncodedStatus:
    """A class to encode status messages."""

    def __init__(self, msg: str):
        self.encoded_msg = EncodedStatusMessage(msg)
        print(self.encoded_msg.init(), flush=True)

    def __enter__(self):
        print(self.encoded_msg.enter(), flush=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(self.encoded_msg.exit(), flush=True)

    def update(self, msg: str):
        print(self.encoded_msg.update(msg), flush=True)

    def stop(self):
        print(self.encoded_msg.stop(), flush=True)

    def start(self):
        print(self.encoded_msg.start(), flush=True)


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


# TODO(zhwu): we need a wrapper for the rich.progress in our code as well.
def safe_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _status
        if _status is None:
            _status = EncodedStatus(msg)
            return _status
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
        if _rich_status is not None and _rich_status._live.is_started:  # pylint: disable=protected-access
            _rich_status.stop()
            yield
            logged = True
            _rich_status.start()
    if not logged:
        yield


class RichSafeStreamHandler(logging.StreamHandler):

    def emit(self, record: logging.LogRecord) -> None:
        with safe_logger():
            return super().emit(record)


def client_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _rich_status
        if _rich_status is None:
            _rich_status = console.status(msg)
        _rich_status.update(msg)
        return _rich_status
    return _NoOpConsoleStatus()


def decode_rich_status(encoded_msg: str) -> Optional[str]:
    """Decode the rich status message."""
    encoded_msg = message_utils.decode_payload(encoded_msg,
                                               raise_for_mismatch=False)
    control, encoded_status = Control.decode(encoded_msg)
    global _rich_status
    if control is None:
        return encoded_msg
    if control == Control.INIT:
        if _rich_status is not None:
            _rich_status.update(encoded_status)
        else:
            _rich_status = console.status(encoded_status)
    else:
        assert _rich_status, f'Rich status not initialized: {encoded_msg}'
        if control == Control.UPDATE:
            _rich_status.update(encoded_status)
        elif control == Control.STOP:
            _rich_status.stop()
        elif control == Control.START:
            _rich_status.start()
    return None
