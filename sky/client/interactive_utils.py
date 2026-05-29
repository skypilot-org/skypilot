"""Utilities for handling interactive SSH authentication."""
import asyncio
import fcntl
import os
import re
import sys
import termios
import threading
import tty
import typing

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import service_account_auth
from sky.server import common as server_common
from sky.utils import rich_utils

if typing.TYPE_CHECKING:
    import websockets
else:
    websockets = adaptors_common.LazyImport('websockets')

logger = sky_logging.init_logger(__name__)

# Global lock to serialize interactive auth on the client side.
# Prevents multiple threads from simultaneously manipulating terminal I/O.
_INTERACTIVE_AUTH_LOCK = threading.Lock()

SKY_INTERACTIVE_PATTERN = re.compile(r'<sky-interactive session="([^"]+)"/>')


# TODO(kevin): Refactor to share code with websocket_proxy.py.
async def _handle_interactive_auth_websocket(session_id: str) -> None:
    """Handle interactive SSH authentication via websocket.

    This establishes a websocket connection to the API server and bridges
    the user's terminal I/O bidirectionally with the PTY on the server,
    allowing interactive authentication (e.g., 2FA).

    Args:
        session_id: The session identifier from the <sky-interactive> signal.
    """
    # Get HTTP server URL and convert to websocket URL
    server_url = server_common.get_server_url()
    server_proto, server_fqdn = server_url.split('://')
    websocket_proto = 'wss' if server_proto == 'https' else 'ws'
    ws_url = (f'{websocket_proto}://{server_fqdn}'
              f'/ssh-interactive-auth?session_id={session_id}')

    logger.info('Starting interactive SSH authentication...')

    headers = {}
    # Add service account auth if available
    headers.update(service_account_auth.get_service_account_headers())
    # Add cookie auth with URL-aware filtering
    headers.update(server_common.get_cookie_header_for_url(ws_url))

    # Set terminal to raw mode if stdin is a tty
    old_settings = None
    if os.isatty(sys.stdin.fileno()):
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())

    stdin_dup_fd = None
    stdout_dup_fd = None
    try:
        # Duplicate stdin/stdout fds before passing to asyncio.
        # When asyncio's loop.connect_read/write_pipe() is called,
        # it creates a transport that takes ownership of the file passed to it.
        # By duplicating the fds, we give asyncio independent copies that it can
        # safely close, while preserving the original sys.stdin/stdout.
        stdin_dup_fd = os.dup(sys.stdin.fileno())
        stdout_dup_fd = os.dup(sys.stdout.fileno())

        async with websockets.connect(ws_url,
                                      additional_headers=headers,
                                      ping_interval=None) as ws:
            loop = asyncio.get_running_loop()

            stdin_reader = asyncio.StreamReader()
            stdin_protocol = asyncio.StreamReaderProtocol(stdin_reader)
            stdin_dup_file = os.fdopen(stdin_dup_fd, 'rb', buffering=0)
            stdin_dup_fd = None  # File object now owns the FD
            await loop.connect_read_pipe(lambda: stdin_protocol, stdin_dup_file)

            stdout_dup_file = os.fdopen(stdout_dup_fd, 'wb', buffering=0)
            stdout_dup_fd = None  # File object now owns the FD
            stdout_transport, stdout_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin,
                stdout_dup_file)  # type: ignore
            stdout_writer = asyncio.StreamWriter(stdout_transport,
                                                 stdout_protocol, None, loop)

            async def stdin_to_websocket():
                """Forward stdin to websocket."""
                try:
                    while True:
                        data = await stdin_reader.read(4096)
                        if not data:
                            break
                        await ws.send(data)
                except asyncio.CancelledError:
                    # Task was cancelled - auth complete
                    pass
                except Exception as e:  # pylint: disable=broad-except
                    logger.debug(f'Error in stdin_to_websocket: {e}')

            async def websocket_to_stdout():
                """Forward websocket to stdout."""
                try:
                    async for message in ws:
                        stdout_writer.write(message)
                        await stdout_writer.drain()
                except Exception as e:  # pylint: disable=broad-except
                    logger.debug(f'Error in websocket_to_stdout: {e}')

            # Run both directions concurrently
            # Use tasks so we can cancel stdin reader when websocket closes
            stdin_task = asyncio.create_task(stdin_to_websocket())
            stdout_task = asyncio.create_task(websocket_to_stdout())

            # Wait for websocket to close (auth complete)
            await stdout_task
            # Cancel stdin reader so it doesn't consume the next keystroke
            stdin_task.cancel()
            try:
                await stdin_task
            except asyncio.CancelledError:
                pass
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to handle interactive authentication: {e}')
        raise
    finally:
        # Restore terminal settings if they were changed
        if old_settings:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                              old_settings)
            # Flush any buffered input from stdin
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
            # Ensure stdout is in blocking mode (can be non-blocking after
            # asyncio transport operations)
            flags = fcntl.fcntl(sys.stdout.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(sys.stdout.fileno(), fcntl.F_SETFL,
                        flags & ~os.O_NONBLOCK)

        for fd in [stdin_dup_fd, stdout_dup_fd]:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    # Already closed by asyncio or never opened
                    pass


def handle_interactive_auth(line: str) -> typing.Optional[str]:
    """Handle interactive SSH authentication signals (sync version).

    Args:
        line: The log line to check for interactive auth markers.

    Returns:
        The line with the marker removed, or None if this was an interactive
        auth signal (meaning the line was consumed).
    """
    match = SKY_INTERACTIVE_PATTERN.search(line)
    if not match:
        return line

    session_id = match.group(1)
    with _INTERACTIVE_AUTH_LOCK:
        # Temporarily stop any spinners to allow terminal I/O
        with rich_utils.safe_logger():
            asyncio.run(_handle_interactive_auth_websocket(session_id))

    return None


async def handle_interactive_auth_async(line: str) -> typing.Optional[str]:
    """Handle interactive SSH authentication signals (async version).

    Args:
        line: The log line to check for interactive auth markers.

    Returns:
        The line with the marker removed, or None if this was an interactive
        auth signal (meaning the line was consumed).
    """
    match = SKY_INTERACTIVE_PATTERN.search(line)
    if not match:
        return line

    session_id = match.group(1)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _INTERACTIVE_AUTH_LOCK.acquire)
    try:
        with rich_utils.safe_logger():
            await _handle_interactive_auth_websocket(session_id)
    finally:
        _INTERACTIVE_AUTH_LOCK.release()

    return None
