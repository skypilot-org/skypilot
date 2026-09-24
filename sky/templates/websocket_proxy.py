#!/usr/bin/env python3
# /// script
# dependencies = [
#   "websockets>=14.0",
# ]
# ///
"""Starting a websocket with SkyPilot API server to proxy SSH to a k8s pod.

This script is useful for users who do not have local Kubernetes credentials.
"""

import asyncio
import json
import os
import struct
import sys
import time
from typing import Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect

from sky import exceptions
from sky.client import service_account_auth
from sky.server import common as server_common
from sky.server import constants
from sky.server.websocket_utils import SSHMessageType
from sky.skylet import constants as skylet_constants

BUFFER_SIZE = 2**16  # 64KB
HEARTBEAT_INTERVAL_SECONDS = 10
MAX_UNANSWERED_PINGS = 100
# Timeout for opening the WebSocket connection. The default (10s) can be
# insufficient when many concurrent SSH connections are established under load,
# causing intermittent "timed out during opening handshake" errors.
OPEN_TIMEOUT_SECONDS = 60


class _RedirectTargetFailed(Exception):
    """The redirect target could not serve this session; fall back.

    Raised when it refuses the handshake, or accepts and closes before
    sending anything back. ``sent`` is what was already read from stdin and
    sent to it -- ssh writes its version banner first, before hearing
    anything -- so the fallback has to replay it: stdin cannot be read twice.
    """

    def __init__(self, reason: str, sent: bytes = b'') -> None:
        super().__init__(reason)
        self.sent = sent


class _Session:
    """What one connection saw: whether the backend sent anything, and the
    stdin bytes sent to it before that (kept only until it does)."""

    def __init__(self) -> None:
        self.got_data = False
        self.sent_before_data = bytearray()


class _Stdio:
    """stdin/stdout, wrapped once for the whole process.

    A fallback connects again after the first connection already read from
    stdin. Wrapping the same pipe twice fails, and would lose what is still
    buffered.
    """
    _instance: Optional['_Stdio'] = None

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    @classmethod
    async def get(cls) -> '_Stdio':
        if cls._instance is None:
            loop = asyncio.get_running_loop()
            # Use asyncio.Stream primitives to wrap stdin and stdout, this is
            # to avoid creating a new thread for each read/write operation
            # excessively.
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
            transport, write_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, sys.stdout)  # type: ignore
            writer = asyncio.StreamWriter(transport, write_protocol, None, loop)
            cls._instance = cls(reader, writer)
        return cls._instance


async def main(
    url: str,
    timestamps_supported: bool,
    login_url: str,
    override_headers: Optional[Dict[str, str]] = None,
    redirect_target: bool = False,
    replay: bytes = b'',
) -> None:
    headers = {}
    if override_headers:
        headers.update(override_headers)
    else:
        headers.update(server_common.get_cookie_header_for_url(url))
        headers.update(service_account_auth.get_service_account_headers())
    try:
        async with connect(url,
                           ping_interval=None,
                           open_timeout=OPEN_TIMEOUT_SECONDS,
                           additional_headers=headers) as websocket:
            session = await run_websocket_proxy(websocket,
                                                timestamps_supported,
                                                replay=replay)
    except websockets.exceptions.InvalidStatus as e:
        if redirect_target:
            raise _RedirectTargetFailed(f'refused: {e}') from e
        if e.response.status_code == 403:
            print(str(exceptions.ApiServerAuthenticationError(login_url)),
                  file=sys.stderr)
        else:
            print(f'Error ssh into cluster: {e}', file=sys.stderr)
        sys.exit(1)
    if redirect_target and not session.got_data:
        raise _RedirectTargetFailed('closed before sending anything',
                                    bytes(session.sent_before_data))


async def run_websocket_proxy(websocket: ClientConnection,
                              timestamps_supported: bool,
                              first_message: Optional[bytes] = None,
                              replay: bytes = b'') -> _Session:
    session = _Session()
    if os.isatty(sys.stdin.fileno()):
        # pylint: disable=import-outside-toplevel
        import termios
        import tty
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    else:
        old_settings = None

    try:
        stdio = await _Stdio.get()
        # Dictionary to store last ping time for latency measurement
        last_ping_time_dict: Optional[Dict[int, float]] = None
        if timestamps_supported:
            last_ping_time_dict = {}

        # Use an Event to signal when websocket is closed
        websocket_closed_event = asyncio.Event()
        websocket_lock = asyncio.Lock()

        if replay:
            # What an earlier, failed connection already took from stdin.
            session.sent_before_data += replay
            async with websocket_lock:
                await websocket.send(_frame(replay, timestamps_supported))

        output = asyncio.create_task(
            websocket_to_stdout(websocket, stdio.writer, timestamps_supported,
                                last_ping_time_dict, websocket_closed_event,
                                websocket_lock, first_message, session))
        others = [
            asyncio.create_task(
                stdin_to_websocket(stdio.reader, websocket,
                                   timestamps_supported, websocket_closed_event,
                                   websocket_lock, session)),
            asyncio.create_task(
                latency_monitor(websocket, last_ping_time_dict,
                                websocket_closed_event, websocket_lock)),
        ]
        await asyncio.gather(output, return_exceptions=True)
        # The socket is closed. The stdin reader may be blocked waiting for
        # ssh, which is itself waiting for the server -- a deadlock that used
        # to hang ssh forever. Stop it; unread bytes stay in the buffer.
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
    finally:
        if old_settings:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                              old_settings)
    return session


def _frame(data: bytes, timestamps_supported: bool) -> bytes:
    if timestamps_supported:
        # Send message with type 0 to indicate data.
        return struct.pack('!B', SSHMessageType.REGULAR_DATA.value) + data
    return data


async def latency_monitor(websocket: ClientConnection,
                          last_ping_time_dict: Optional[dict],
                          websocket_closed_event: asyncio.Event,
                          websocket_lock: asyncio.Lock):
    """Periodically send PING messages (type 1) to measure latency."""
    if last_ping_time_dict is None:
        return
    next_id = 0
    while not websocket_closed_event.is_set():
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if len(last_ping_time_dict) >= MAX_UNANSWERED_PINGS:
                # We are not getting responses, clear the dictionary so
                # as not to grow unbounded.
                last_ping_time_dict.clear()
            # monotonic(), not time(): both ends of this interval are read in
            # this process, and the wall clock can step (NTP) or jump (laptop
            # suspend/resume) mid-window. A step charges the adjustment to SSH
            # latency; a backwards step makes the interval negative, which
            # wraps when packed into the unsigned '!Q' below.
            ping_time = time.monotonic()
            next_id += 1
            last_ping_time_dict[next_id] = ping_time
            message_header_bytes = struct.pack('!BI',
                                               SSHMessageType.PINGPONG.value,
                                               next_id)
            try:
                async with websocket_lock:
                    await websocket.send(message_header_bytes)
            except websockets.exceptions.ConnectionClosed as e:
                # Websocket is already closed.
                print(f'Failed to send PING message: {e}', file=sys.stderr)
                break
        except Exception as e:
            print(f'Error in latency_monitor: {e}', file=sys.stderr)
            websocket_closed_event.set()
            raise e


async def stdin_to_websocket(reader: asyncio.StreamReader,
                             websocket: ClientConnection,
                             timestamps_supported: bool,
                             websocket_closed_event: asyncio.Event,
                             websocket_lock: asyncio.Lock,
                             session: Optional[_Session] = None):
    try:
        while not websocket_closed_event.is_set():
            # Read at most BUFFER_SIZE bytes, this not affect
            # responsiveness since it will return as soon as
            # there is at least one byte.
            # The BUFFER_SIZE is chosen to be large enough to improve
            # throughput.
            data = await reader.read(BUFFER_SIZE)

            if not data:
                break
            if session is not None and not session.got_data:
                # Kept, before the send, for a fallback to replay.
                session.sent_before_data += data
            async with websocket_lock:
                await websocket.send(_frame(data, timestamps_supported))

    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in stdin_to_websocket: {e}', file=sys.stderr)
    finally:
        async with websocket_lock:
            await websocket.close()
        websocket_closed_event.set()


async def websocket_to_stdout(websocket: ClientConnection,
                              writer: asyncio.StreamWriter,
                              timestamps_supported: bool,
                              last_ping_time_dict: Optional[dict],
                              websocket_closed_event: asyncio.Event,
                              websocket_lock: asyncio.Lock,
                              first_message: Optional[bytes] = None,
                              session: Optional[_Session] = None):
    try:
        # If we already received a first message (e.g. from redirect check),
        # process it before entering the recv loop.
        pending_message = first_message
        while not websocket_closed_event.is_set():
            if pending_message is not None:
                message = pending_message
                pending_message = None
            else:
                message = await websocket.recv()
            if (timestamps_supported and len(message) > 0 and
                    last_ping_time_dict is not None):
                message_type = struct.unpack('!B', message[:1])[0]
                if message_type == SSHMessageType.REGULAR_DATA.value:
                    # Regular data - strip type byte and write to stdout
                    message = message[1:]
                elif message_type == SSHMessageType.PINGPONG.value:
                    # PONG response - calculate latency and send measurement
                    if not len(message) == struct.calcsize('!BI'):
                        raise ValueError(
                            f'Invalid PONG message length: {len(message)}')
                    pong_id = struct.unpack('!I', message[1:5])[0]
                    pong_time = time.monotonic()

                    ping_time = last_ping_time_dict.pop(pong_id, None)

                    if ping_time is None:
                        continue

                    latency_seconds = pong_time - ping_time
                    latency_ms = int(latency_seconds * 1000)

                    # Send latency measurement (type 2)
                    message_type_bytes = struct.pack(
                        '!B', SSHMessageType.LATENCY_MEASUREMENT.value)
                    latency_bytes = struct.pack('!Q', latency_ms)
                    message = message_type_bytes + latency_bytes
                    # Send to server.
                    async with websocket_lock:
                        await websocket.send(message)
                    continue
            # No timestamps support, write directly
            if session is not None and message and not session.got_data:
                session.got_data = True
                session.sent_before_data.clear()
            writer.write(message)
            await writer.drain()
    except websockets.exceptions.ConnectionClosed:
        print('WebSocket connection closed', file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in websocket_to_stdout: {e}', file=sys.stderr)
        raise e
    finally:
        async with websocket_lock:
            await websocket.close()
        websocket_closed_event.set()


async def _connect_with_redirect(ws_url: str, timestamps_supported: bool,
                                 login_url: str) -> None:
    """Connect to WebSocket, handle REDIRECT frame if server sends one."""
    headers: Dict[str, str] = {}
    headers.update(server_common.get_cookie_header_for_url(ws_url))
    headers.update(service_account_auth.get_service_account_headers())
    try:
        async with connect(ws_url,
                           ping_interval=None,
                           open_timeout=OPEN_TIMEOUT_SECONDS,
                           additional_headers=headers) as websocket:
            # Read the first frame to check for REDIRECT.
            first_msg = await websocket.recv()
            if (len(first_msg) > 0 and struct.unpack('!B', first_msg[:1])[0]
                    == SSHMessageType.REDIRECT):
                redirect_info = json.loads(first_msg[1:].decode())
                await websocket.close()
                await _handle_redirect(redirect_info,
                                       timestamps_supported,
                                       login_url,
                                       original_url=ws_url)
                return
            await run_websocket_proxy(websocket,
                                      timestamps_supported,
                                      first_message=first_msg)
    except websockets.exceptions.InvalidStatus as e:
        if e.response.status_code == 403:
            print(str(exceptions.ApiServerAuthenticationError(login_url)),
                  file=sys.stderr)
        else:
            print(f'Error ssh into cluster: {e}', file=sys.stderr)
        sys.exit(1)


async def _handle_redirect(redirect_info: dict,
                           timestamps_supported: bool,
                           login_url: str,
                           original_url: str = '') -> None:
    """Reconnect after receiving a REDIRECT frame.

    The redirect_info dict is opaque to this module — it contains a ready-to-use
    ``url`` (full WebSocket URL) and ``headers`` (e.g. authorization) provided
    by the server-side redirect hook.
    """
    url = redirect_info['url']
    headers = redirect_info.get('headers', {})
    try:
        await main(
            url,
            timestamps_supported,
            login_url,
            override_headers=headers,
            redirect_target=True,
        )
    except (OSError, websockets.exceptions.InvalidURI,
            websockets.exceptions.InvalidHandshake, asyncio.TimeoutError,
            _RedirectTargetFailed) as e:
        # The redirect target is unreachable, refused us, or closed before
        # serving anything: fall back to the API server, which can serve the
        # session itself.
        if not original_url:
            raise
        separator = '&' if '?' in original_url else '?'
        fallback_url = f'{original_url}{separator}no_redirect=1'
        await main(fallback_url,
                   timestamps_supported,
                   login_url,
                   replay=getattr(e, 'sent', b''))


if __name__ == '__main__':
    server_url = sys.argv[1].strip('/')

    disable_latency_measurement = os.environ.get(
        skylet_constants.SSH_DISABLE_LATENCY_MEASUREMENT_ENV_VAR, '0') == '1'

    # Capture the original API server URL for login hint if authentication
    # is required.
    _login_url = server_url
    server_proto, server_fqdn = server_url.split('://')
    websocket_proto = 'ws'
    if server_proto == 'https':
        websocket_proto = 'wss'
    server_url = f'{websocket_proto}://{server_fqdn}'
    client_version_str = f'&client_version={constants.API_VERSION}'

    # For backwards compatibility, fallback to kubernetes-pod-ssh-proxy if
    # no endpoint is provided.
    endpoint = sys.argv[3] if len(sys.argv) > 3 else 'kubernetes-pod-ssh-proxy'
    # Worker index for Slurm.
    worker_idx = sys.argv[4] if len(sys.argv) > 4 else '0'
    websocket_url = (f'{server_url}/{endpoint}'
                     f'?cluster_name={sys.argv[2]}'
                     f'&worker={worker_idx}'
                     f'{client_version_str}')

    asyncio.run(
        _connect_with_redirect(websocket_url, not disable_latency_measurement,
                               _login_url))
