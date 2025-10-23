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

from sky.server import constants

BUFFER_SIZE = 2**16  # 64KB
HEARTBEAT_INTERVAL_SECONDS = 5

# Environment variable for a file path to the API cookie file.
# Keep in sync with server/constants.py
API_COOKIE_FILE_ENV_VAR = 'SKYPILOT_API_COOKIE_FILE'
# Default file if unset.
# Keep in sync with server/constants.py
API_COOKIE_FILE_DEFAULT_LOCATION = '~/.sky/cookies.txt'


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


async def main(url: str, timestamps_supported: bool) -> None:
    cookie_header = _get_cookie_header(url)
    async with connect(url,
                       ping_interval=None,
                       additional_headers=cookie_header) as websocket:
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
            stdout_writer = asyncio.StreamWriter(transport, protocol, None,
                                                 loop)
            latency_send_time_queue : Optional[asyncio.Queue[float]] = None
            latency_results_queue : Optional[asyncio.Queue[float]] = None
            if timestamps_supported:
                latency_send_time_queue = asyncio.Queue(maxsize=1024)
                latency_results_queue = asyncio.Queue(maxsize=1024)

            websocket_closed = False

            await asyncio.gather(
                stdin_to_websocket(stdin_reader, websocket,
                                   timestamps_supported,
                                   latency_send_time_queue,
                                   websocket_closed),
                websocket_to_stdout(websocket, stdout_writer, 
                    timestamps_supported, latency_send_time_queue, latency_results_queue, websocket_closed),
                latency_monitor(websocket, latency_results_queue, websocket_closed)
            )
        finally:
            if old_settings:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  old_settings)

async def latency_monitor(websocket: ClientConnection,
                          latency_results_queue: Optional[asyncio.Queue[float]],
                          websocket_closed: bool):
    if latency_results_queue is None:
        return
    while not websocket_closed:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        while True:
            # Grab all the latency results from the queue.
            results = []
            try:
                print(f'Latency results queue size: {latency_results_queue.qsize()}')
                print(f'Websocket closed: {websocket_closed}')
                results.append(latency_results_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        # raise Exception(f'Results: {results}')
        if len(results) == 0:
            continue
        average_latency = sum(results) / len(results)
        print(f'Average latency: {average_latency}')
        if average_latency > 0:
            average_latency_ms = int(average_latency * 1000)
            message_type_bytes = struct.pack('!B', 1)
            latency_bytes = struct.pack('!Q', average_latency_ms)
            data = message_type_bytes + latency_bytes
            await websocket.send(data)
        else:
            break

async def stdin_to_websocket(reader: asyncio.StreamReader,
                             websocket: ClientConnection,
                             timestamps_supported: bool,
                             latency_send_time_queue: Optional[asyncio.Queue[float]],
                             websocket_closed: bool):
    try:
        while not websocket_closed:
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
                send_time = time.time()
                message_type_bytes = struct.pack('!B', 0)
                data = message_type_bytes + data
                await websocket.send(data)
                try:
                    latency_send_time_queue.put_nowait(send_time)
                except asyncio.QueueFull:
                    pass
            else:
                await websocket.send(data)

    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in stdin_to_websocket: {e}', file=sys.stderr)
    finally:
        await websocket.close()
        websocket_closed = True


async def websocket_to_stdout(websocket: ClientConnection,
                              writer: asyncio.StreamWriter,
                              timestamps_supported: bool,
                              latency_send_time_queue: Optional[asyncio.Queue[float]],
                              latency_results_queue: Optional[asyncio.Queue[float]],
                              websocket_closed: bool):
    try:
        while not websocket_closed:
            message = await websocket.recv()
            writer.write(message)
            await writer.drain()
            if timestamps_supported:
                receive_time = time.time()
                try:
                    send_time = latency_send_time_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # We don't have a send time to match this receive time.
                    pass
                # raise Exception(f'Send time: {send_time}, Receive time: {receive_time}')
                try:
                    latency_results_queue.put_nowait(receive_time - send_time)
                except asyncio.QueueFull:
                    # We have too many latency results to process.
                    pass
                # raise Exception(f'Latency results queue size: {latency_results_queue.qsize()}')
                # except asyncio.QueueFull:
                #     # We have too many latency results to process.
                #     pass
    except websockets.exceptions.ConnectionClosed:
        print('WebSocket connection closed', file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except
        # print(f'Error in websocket_to_stdout: {e}', file=sys.stderr)
        raise e
    finally:
        await websocket.close()
        print(f'Websocket closed manually: {websocket_closed}')
        websocket_closed = True


if __name__ == '__main__':
    server_url = sys.argv[1].strip('/')
    if '://' not in server_url:
        # Keep backward compatibility for legacy server URLs without protocol
        # TODO(aylei): Remove this after 0.10.0
        server_url = f'http://{server_url}'

    health_url = f'{server_url}/api/health'
    health_response = requests.get(health_url)
    health_data = health_response.json()
    timestamps_are_supported = int(health_data['api_version']) > 20
    print(f'Timestamps are supported: {timestamps_are_supported}')

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
    asyncio.run(main(websocket_url, timestamps_are_supported))
