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
from http.cookiejar import MozillaCookieJar
import os
import struct
import sys
import time
from typing import Dict, Optional
from urllib.request import Request

import requests
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect

from sky import exceptions
from sky.client import service_account_auth
from sky.server import constants
from sky.server.server import KubernetesSSHMessageType
from sky.skylet import constants as skylet_constants

BUFFER_SIZE = 2**16  # 64KB
HEARTBEAT_INTERVAL_SECONDS = 10

# Environment variable for a file path to the API cookie file.
# Keep in sync with server/constants.py
API_COOKIE_FILE_ENV_VAR = 'SKYPILOT_API_COOKIE_FILE'
# Default file if unset.
# Keep in sync with server/constants.py
API_COOKIE_FILE_DEFAULT_LOCATION = '~/.sky/cookies.txt'

MAX_UNANSWERED_PINGS = 100


def _get_cookie_header(url: str) -> Dict[str, str]:
    """Extract Cookie header value from a cookie jar for a specific URL"""
    cookie_path = os.environ.get(API_COOKIE_FILE_ENV_VAR)
    if cookie_path is None:
        cookie_path = API_COOKIE_FILE_DEFAULT_LOCATION
    cookie_path = os.path.expanduser(cookie_path)
    if not os.path.exists(cookie_path):
        return {}

    request = Request(url)
    cookie_jar = MozillaCookieJar(os.path.expanduser(cookie_path))
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    cookie_jar.add_cookie_header(request)
    cookie_header = request.get_header('Cookie')
    # if cookie file is empty, return empty dict
    if cookie_header is None:
        return {}
    return {'Cookie': cookie_header}


async def main(url: str, timestamps_supported: bool, login_url: str) -> None:
    headers = {}
    headers.update(_get_cookie_header(url))
    headers.update(service_account_auth.get_service_account_headers())
    try:
        async with connect(url, ping_interval=None,
                           additional_headers=headers) as websocket:
            await run_websocket_proxy(websocket, timestamps_supported)
    except websockets.exceptions.InvalidStatus as e:
        if e.response.status_code == 403:
            print(str(exceptions.ApiServerAuthenticationError(login_url)),
                  file=sys.stderr)
        else:
            print(f'Error ssh into cluster: {e}', file=sys.stderr)
        sys.exit(1)


async def run_websocket_proxy(websocket: ClientConnection,
                              timestamps_supported: bool) -> None:
    if os.isatty(sys.stdin.fileno()):
        # pylint: disable=import-outside-toplevel
        import termios
        import tty
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    else:
        old_settings = None

    try:
        loop = asyncio.get_running_loop()
        # Use asyncio.Stream primitives to wrap stdin and stdout, this is to
        # avoid creating a new thread for each read/write operation
        # excessively.
        stdin_reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stdin_reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        transport, protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout)  # type: ignore
        stdout_writer = asyncio.StreamWriter(transport, protocol, None, loop)
        # Dictionary to store last ping time for latency measurement
        last_ping_time_dict: Optional[Dict[int, float]] = None
        if timestamps_supported:
            last_ping_time_dict = {}

        # Use an Event to signal when websocket is closed
        websocket_closed_event = asyncio.Event()
        websocket_lock = asyncio.Lock()

        await asyncio.gather(
            stdin_to_websocket(stdin_reader, websocket, timestamps_supported,
                               websocket_closed_event, websocket_lock),
            websocket_to_stdout(websocket, stdout_writer, timestamps_supported,
                                last_ping_time_dict, websocket_closed_event,
                                websocket_lock),
            latency_monitor(websocket, last_ping_time_dict,
                            websocket_closed_event, websocket_lock),
            return_exceptions=True)
    finally:
        if old_settings:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                              old_settings)


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
            ping_time = time.time()
            next_id += 1
            last_ping_time_dict[next_id] = ping_time
            message_header_bytes = struct.pack(
                '!BI', KubernetesSSHMessageType.PINGPONG.value, next_id)
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
                             websocket_lock: asyncio.Lock):
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
            if timestamps_supported:
                # Send message with type 0 to indicate data.
                message_type_bytes = struct.pack(
                    '!B', KubernetesSSHMessageType.REGULAR_DATA.value)
                data = message_type_bytes + data
            async with websocket_lock:
                await websocket.send(data)

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
                              websocket_lock: asyncio.Lock):
    try:
        while not websocket_closed_event.is_set():
            message = await websocket.recv()
            if (timestamps_supported and len(message) > 0 and
                    last_ping_time_dict is not None):
                message_type = struct.unpack('!B', message[:1])[0]
                if message_type == KubernetesSSHMessageType.REGULAR_DATA.value:
                    # Regular data - strip type byte and write to stdout
                    message = message[1:]
                elif message_type == KubernetesSSHMessageType.PINGPONG.value:
                    # PONG response - calculate latency and send measurement
                    if not len(message) == struct.calcsize('!BI'):
                        raise ValueError(
                            f'Invalid PONG message length: {len(message)}')
                    pong_id = struct.unpack('!I', message[1:5])[0]
                    pong_time = time.time()

                    ping_time = last_ping_time_dict.pop(pong_id, None)

                    if ping_time is None:
                        continue

                    latency_seconds = pong_time - ping_time
                    latency_ms = int(latency_seconds * 1000)

                    # Send latency measurement (type 2)
                    message_type_bytes = struct.pack(
                        '!B',
                        KubernetesSSHMessageType.LATENCY_MEASUREMENT.value)
                    latency_bytes = struct.pack('!Q', latency_ms)
                    message = message_type_bytes + latency_bytes
                    # Send to server.
                    async with websocket_lock:
                        await websocket.send(message)
                    continue
            # No timestamps support, write directly
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


if __name__ == '__main__':
    server_url = sys.argv[1].strip('/')
    if '://' not in server_url:
        # Keep backward compatibility for legacy server URLs without protocol
        # TODO(aylei): Remove this after 0.10.0
        server_url = f'http://{server_url}'

    disable_latency_measurement = os.environ.get(
        skylet_constants.SSH_DISABLE_LATENCY_MEASUREMENT_ENV_VAR, '0') == '1'
    if disable_latency_measurement:
        timestamps_are_supported = False
    else:
        # TODO(aylei): remove the separate /api/health call and use the header
        # during websocket handshake to determine the server version.
        health_url = f'{server_url}/api/health'
        cookie_hdr = _get_cookie_header(health_url)
        health_response = requests.get(health_url, headers=cookie_hdr)
        health_data = health_response.json()
        timestamps_are_supported = int(health_data.get('api_version', 0)) > 21

    # Capture the original API server URL for login hint if authentication
    # is required.
    _login_url = server_url
    server_proto, server_fqdn = server_url.split('://')
    websocket_proto = 'ws'
    if server_proto == 'https':
        websocket_proto = 'wss'
    server_url = f'{websocket_proto}://{server_fqdn}'

    client_version_str = (f'&client_version={constants.API_VERSION}'
                          if timestamps_are_supported else '')

    websocket_url = (f'{server_url}/kubernetes-pod-ssh-proxy'
                     f'?cluster_name={sys.argv[2]}'
                     f'{client_version_str}')
    asyncio.run(main(websocket_url, timestamps_are_supported, _login_url))
