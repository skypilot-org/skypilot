"""Rich status spinner utils."""
import contextlib
import enum
import logging
import threading
from typing import Dict, Optional, Tuple, Union

import rich.console as rich_console

from sky.utils import message_utils

console = rich_console.Console(soft_wrap=True)
_statuses: Dict[str, Optional[Union['EncodedStatus',
                                    'rich_console.Status']]] = {
                                        'server': None,
                                        'client': None,
                                    }
_decoding_status: Optional['_RevertibleStatus'] = None
_status_nesting_level = 0

_logging_lock = threading.RLock()


class Control(enum.Enum):
    """Control codes for the status spinner."""
    INIT = 'rich_init'
    START = 'rich_start'
    STOP = 'rich_stop'
    EXIT = 'rich_exit'
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
        return message_utils.encode_payload(Control.EXIT.encode(''))

    def update(self, msg: str) -> str:
        return message_utils.encode_payload(Control.UPDATE.encode(msg))

    def stop(self) -> str:
        return message_utils.encode_payload(Control.STOP.encode(''))

    def start(self) -> str:
        return message_utils.encode_payload(Control.START.encode(self.msg))


class EncodedStatus:
    """A class to encode status messages."""

    def __init__(self, msg: str):
        self.status = msg
        self.encoded_msg = EncodedStatusMessage(msg)
        print(self.encoded_msg.init(), flush=True)

    def __enter__(self):
        print(self.encoded_msg.enter(), flush=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(self.encoded_msg.exit(), flush=True)

    def update(self, msg: str):
        self.status = msg
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
class _RevertibleStatus:
    """A wrapper for status that can revert to previous message after exit."""

    def __init__(self, message: str, status_type: str):
        self.previous_message = None
        self.status_type = status_type
        status = _statuses[status_type]
        if status is not None:
            self.previous_message = status.status
        self.message = message

    def __enter__(self):
        global _status_nesting_level
        _statuses[self.status_type].update(self.message)
        _status_nesting_level += 1
        _statuses[self.status_type].__enter__()
        return _statuses[self.status_type]

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _status_nesting_level
        _status_nesting_level -= 1
        if _status_nesting_level <= 0:
            _status_nesting_level = 0
            if _statuses[self.status_type] is not None:
                _statuses[self.status_type].__exit__(exc_type, exc_val, exc_tb)
                _statuses[self.status_type] = None
        else:
            _statuses[self.status_type].update(self.previous_message)

    def update(self, *args, **kwargs):
        _statuses[self.status_type].update(*args, **kwargs)

    def stop(self):
        _statuses[self.status_type].stop()

    def start(self):
        _statuses[self.status_type].start()


def safe_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        if _statuses['server'] is None:
            _statuses['server'] = EncodedStatus(msg)
        return _RevertibleStatus(msg, 'server')
    return _NoOpConsoleStatus()


def stop_safe_status():
    """Stops all nested statuses.

    This is useful when we need to stop all statuses, e.g., when we are going to
    stream logs from user program and do not want it to interfere with the
    spinner display.
    """
    if (threading.current_thread() is threading.main_thread() and
            _statuses['server'] is not None):
        _statuses['server'].stop()


def force_update_status(msg: str):
    """Update the status message even if sky_logging.is_silent() is true."""
    if (threading.current_thread() is threading.main_thread() and
            _statuses['server'] is not None):
        _statuses['server'].update(msg)


@contextlib.contextmanager
def safe_logger():
    with _logging_lock:
        client_status_obj = _statuses['client']

        client_status_live = (client_status_obj is not None and
                              client_status_obj._live.is_started)  # pylint: disable=protected-access
        if client_status_live:
            client_status_obj.stop()
        yield
        if client_status_live:
            client_status_obj.start()


class RichSafeStreamHandler(logging.StreamHandler):

    def emit(self, record: logging.LogRecord) -> None:
        with safe_logger():
            return super().emit(record)


def client_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        if _statuses['client'] is None:
            _statuses['client'] = console.status(msg)
        return _RevertibleStatus(msg, 'client')
    return _NoOpConsoleStatus()


def decode_rich_status(encoded_msg: str) -> Optional[str]:
    """Decode the rich status message."""
    encoded_msg = message_utils.decode_payload(encoded_msg,
                                               raise_for_mismatch=False)
    # print(f'encoded_msg: {encoded_msg}', flush=True)
    control, encoded_status = Control.decode(encoded_msg)
    global _decoding_status
    if control is None:
        return encoded_msg

    if threading.current_thread() is not threading.main_thread():
        if control is not None:
            return None
        else:
            return encoded_msg

    if control == Control.INIT:
        if _statuses['client'] is None:
            _statuses['client'] = console.status(encoded_status)
        _decoding_status = _RevertibleStatus(encoded_status, 'client')
    else:
        assert _decoding_status is not None, (
            f'Rich status not initialized: {encoded_msg}')
        if control == Control.UPDATE:
            _decoding_status.update(encoded_status)
        elif control == Control.STOP:
            _decoding_status.stop()
        elif control == Control.EXIT:
            _decoding_status.__exit__(None, None, None)
            _decoding_status = None
        elif control == Control.START:
            _decoding_status.start()
    return None
