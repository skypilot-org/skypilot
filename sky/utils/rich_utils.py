"""Rich status spinner utils."""
import contextlib
import enum
import logging
import threading
import typing
from typing import Dict, Iterator, Optional, Tuple, Union

from sky.adaptors import common as adaptors_common
from sky.utils import annotations
from sky.utils import message_utils
from sky.utils import rich_console_utils

if typing.TYPE_CHECKING:
    import requests
    import rich.console as rich_console
else:
    requests = adaptors_common.LazyImport('requests')
    rich_console = adaptors_common.LazyImport('rich.console')

_statuses: Dict[str, Optional[Union['EncodedStatus',
                                    'rich_console.Status']]] = {
                                        'server': None,
                                        'client': None,
                                    }
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
        print(self.encoded_msg.init(), end='', flush=True)

    def __enter__(self):
        print(self.encoded_msg.enter(), end='', flush=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(self.encoded_msg.exit(), end='', flush=True)

    def update(self, msg: str):
        self.status = msg
        print(self.encoded_msg.update(msg), end='', flush=True)

    def stop(self):
        print(self.encoded_msg.stop(), end='', flush=True)

    def start(self):
        print(self.encoded_msg.start(), end='', flush=True)


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


# TODO(SKY-1216): we need a wrapper for the rich.progress in our code as well.
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
        # We use the same lock with the `safe_logger` to avoid the following 2
        # raice conditions. We refer loggers in another thread as "thread
        # logger" hereafter.
        # 1. When a thread logger stopped the status in `safe_logger`, and
        #    here we exit the status and set it to None. Then the thread logger
        #    will raise an error when it tries to restart the status.
        # 2. When a thread logger stopped the status in `safe_logger`, and
        #    here we exit the status and entered a new one. Then the thread
        #    logger will raise an error when it tries to restart the old status,
        #    since only one LiveStatus can be started at the same time.
        # Please refer to #4995 for more information.
        with _logging_lock:
            global _status_nesting_level
            _status_nesting_level -= 1
            if _status_nesting_level <= 0:
                _status_nesting_level = 0
                if _statuses[self.status_type] is not None:
                    _statuses[self.status_type].__exit__(
                        exc_type, exc_val, exc_tb)
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
    if (annotations.is_on_api_server and
            threading.current_thread() is threading.main_thread() and
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
            _statuses['client'] = rich_console_utils.get_console().status(msg)
        return _RevertibleStatus(msg, 'client')
    return _NoOpConsoleStatus()


def decode_rich_status(
        response: 'requests.Response') -> Iterator[Optional[str]]:
    """Decode the rich status message from the response."""
    decoding_status = None
    try:
        last_line = ''
        # Buffer to store incomplete UTF-8 bytes between chunks
        undecoded_buffer = b''

        # Iterate over the response content in chunks. We do not use iter_lines
        # because it will strip the trailing newline characters, causing the
        # progress bar ending with `\r` becomes a pyramid.
        for chunk in response.iter_content(chunk_size=None):
            if chunk is None:
                return

            # Append the new chunk to any leftover bytes from previous iteration
            current_bytes = undecoded_buffer + chunk
            undecoded_buffer = b''

            # Try to decode the combined bytes
            try:
                encoded_msg = current_bytes.decode('utf-8')
            except UnicodeDecodeError as e:
                # Check if this is potentially an incomplete sequence at the end
                if e.start > 0:
                    # Decode the valid part
                    encoded_msg = current_bytes[:e.start].decode('utf-8')

                    # Check if the remaining bytes are likely a partial char
                    # or actually invalid UTF-8
                    remaining_bytes = current_bytes[e.start:]
                    if len(remaining_bytes) < 4:  # Max UTF-8 char is 4 bytes
                        # Likely incomplete - save for next chunk
                        undecoded_buffer = remaining_bytes
                    else:
                        # Likely invalid - replace with replacement character
                        encoded_msg += remaining_bytes.decode('utf-8',
                                                              errors='replace')
                        undecoded_buffer = b''
                else:
                    # Error at the very beginning of the buffer - invalid UTF-8
                    encoded_msg = current_bytes.decode('utf-8',
                                                       errors='replace')
                    undecoded_buffer = b''

            lines = encoded_msg.splitlines(keepends=True)

            # Skip processing if lines is empty to avoid IndexError
            if not lines:
                continue

            # Append any leftover text from previous chunk to first line
            lines[0] = last_line + lines[0]
            last_line = lines[-1]
            # If the last line is not ended with `\r` or `\n` (with ending
            # spaces stripped), it means the last line is not a complete line.
            # We keep the last line in the buffer and continue.
            if (not last_line.strip(' ').endswith('\r') and
                    not last_line.strip(' ').endswith('\n')):
                lines = lines[:-1]
            else:
                # Reset the buffer for the next line, as the last line is a
                # complete line.
                last_line = ''

            for line in lines:
                if line.endswith('\r\n'):
                    # Replace `\r\n` with `\n`, as printing a line ends with
                    # `\r\n` in linux will cause the line to be empty.
                    line = line[:-2] + '\n'
                is_payload, line = message_utils.decode_payload(
                    line, raise_for_mismatch=False)
                control = None
                if is_payload:
                    control, encoded_status = Control.decode(line)
                if control is None:
                    yield line
                    continue

                # control is not None, i.e. it is a rich status control message.
                if threading.current_thread() is not threading.main_thread():
                    yield None
                    continue
                if control == Control.INIT:
                    decoding_status = client_status(encoded_status)
                else:
                    if decoding_status is None:
                        # status may not be initialized if a user use --tail for
                        # sky api logs.
                        continue
                    assert decoding_status is not None, (
                        f'Rich status not initialized: {line}')
                    if control == Control.UPDATE:
                        decoding_status.update(encoded_status)
                    elif control == Control.STOP:
                        decoding_status.stop()
                    elif control == Control.EXIT:
                        decoding_status.__exit__(None, None, None)
                    elif control == Control.START:
                        decoding_status.start()
    finally:
        if decoding_status is not None:
            decoding_status.__exit__(None, None, None)
