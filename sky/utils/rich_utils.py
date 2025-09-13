"""Rich status spinner utils."""
import contextlib
import contextvars
import enum
import logging
import threading
import typing
from typing import Callable, Iterator, Optional, Tuple, Union

from sky import exceptions
from sky.adaptors import common as adaptors_common
from sky.utils import annotations
from sky.utils import context
from sky.utils import message_utils
from sky.utils import rich_console_utils

if typing.TYPE_CHECKING:
    import aiohttp
    import requests
    import rich.console as rich_console
else:
    requests = adaptors_common.LazyImport('requests')
    rich_console = adaptors_common.LazyImport('rich.console')
    aiohttp = adaptors_common.LazyImport('aiohttp')

GeneralStatus = Union['rich_console.Status', 'EncodedStatus']

_client_status: Optional[GeneralStatus] = None
_server_status: contextvars.ContextVar[
    Optional[GeneralStatus]] = contextvars.ContextVar('server_status',
                                                      default=None)


def _get_client_status() -> Optional[GeneralStatus]:
    return _client_status


def _get_server_status() -> Optional[GeneralStatus]:
    return _server_status.get()


def _set_client_status(status: Optional[GeneralStatus]):
    global _client_status
    _client_status = status


def _set_server_status(status: Optional[GeneralStatus]):
    _server_status.set(status)


_status_nesting_level = 0

_logging_lock = threading.RLock()


class Control(enum.Enum):
    """Control codes for the status spinner."""
    INIT = 'rich_init'
    START = 'rich_start'
    STOP = 'rich_stop'
    EXIT = 'rich_exit'
    UPDATE = 'rich_update'
    HEARTBEAT = 'heartbeat'
    RETRY = 'retry'

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

    def __init__(self, message: str, get_status_fn: Callable[[], GeneralStatus],
                 set_status_fn: Callable[[Optional[GeneralStatus]], None]):
        self.previous_message = None
        self.get_status_fn = get_status_fn
        self.set_status_fn = set_status_fn
        status = self.get_status_fn()
        if status is not None:
            self.previous_message = status.status
        self.message = message

    def __enter__(self):
        global _status_nesting_level
        self.get_status_fn().update(self.message)
        _status_nesting_level += 1
        self.get_status_fn().__enter__()
        return self.get_status_fn()

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
                if self.get_status_fn() is not None:
                    self.get_status_fn().__exit__(exc_type, exc_val, exc_tb)
                    self.set_status_fn(None)
            else:
                self.get_status_fn().update(self.previous_message)

    def update(self, *args, **kwargs):
        self.get_status_fn().update(*args, **kwargs)

    def stop(self):
        self.get_status_fn().stop()

    def start(self):
        self.get_status_fn().start()


def _is_thread_safe() -> bool:
    """Check if the current status context is thread-safe.

    We are thread-safe if we are on the main thread or the server_status is
    context-local, i.e. an async context has been initialized.
    """
    return (threading.current_thread() is threading.main_thread() or
            context.get() is not None)


def safe_status(msg: str) -> Union['rich_console.Status', _NoOpConsoleStatus]:
    """A wrapper for multi-threaded server-side console.status.

    This function will encode rich status with control codes and output the
    encoded string to stdout. Client-side decode control codes from server
    output and update the rich status. This function is safe to be called in
    async/multi-threaded context.

    See also: :func:`client_status`, :class:`EncodedStatus`.
    """
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (annotations.is_on_api_server and _is_thread_safe() and
            not sky_logging.is_silent()):
        if _get_server_status() is None:
            _set_server_status(EncodedStatus(msg))
        return _RevertibleStatus(msg, _get_server_status, _set_server_status)
    return _NoOpConsoleStatus()


def stop_safe_status():
    """Stops all nested statuses.

    This is useful when we need to stop all statuses, e.g., when we are going to
    stream logs from user program and do not want it to interfere with the
    spinner display.
    """
    if _is_thread_safe():
        return
    server_status = _get_server_status()
    if server_status is not None:
        server_status.stop()


def force_update_status(msg: str):
    """Update the status message even if sky_logging.is_silent() is true."""
    if not _is_thread_safe():
        return
    server_status = _get_server_status()
    if server_status is not None:
        server_status.update(msg)


@contextlib.contextmanager
def safe_logger():
    with _logging_lock:
        client_status_obj = _get_client_status()

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
    """A wrapper for multi-threaded client-side console.status."""
    from sky import sky_logging  # pylint: disable=import-outside-toplevel
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        if _get_client_status() is None:
            _set_client_status(rich_console_utils.get_console().status(msg))
        return _RevertibleStatus(msg, _get_client_status, _set_client_status)
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

                if control == Control.RETRY:
                    raise exceptions.RequestInterruptedError(
                        'Streaming interrupted. Please retry.')
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
                    elif control == Control.HEARTBEAT:
                        # Heartbeat is not displayed to the user, so we do not
                        # need to update the status.
                        pass
    finally:
        if decoding_status is not None:
            decoding_status.__exit__(None, None, None)


async def decode_rich_status_async(
        response: 'aiohttp.ClientResponse'
) -> typing.AsyncIterator[Optional[str]]:
    """Async version of rich_utils.decode_rich_status that decodes rich status
    messages from an aiohttp response.

    Args:
        response: The aiohttp response.

    Yields:
        Optional[str]: Decoded lines or None for control messages.
    """
    decoding_status = None
    try:
        last_line = ''
        # Buffer to store incomplete UTF-8 bytes between chunks
        undecoded_buffer = b''

        # Iterate over the response content in chunks
        async for chunk, _ in response.content.iter_chunks():
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
                if line is None:
                    continue
                control = None
                if is_payload:
                    control, encoded_status = Control.decode(line)
                if control is None:
                    yield line
                    continue

                if control == Control.RETRY:
                    raise exceptions.RequestInterruptedError(
                        'Streaming interrupted. Please retry.')
                # control is not None, i.e. it is a rich status control message.
                # In async context, we'll handle rich status controls normally
                # since async typically runs in main thread
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
                    elif control == Control.HEARTBEAT:
                        # Heartbeat is not displayed to the user, so we do not
                        # need to update the status.
                        pass
    finally:
        if decoding_status is not None:
            decoding_status.__exit__(None, None, None)
