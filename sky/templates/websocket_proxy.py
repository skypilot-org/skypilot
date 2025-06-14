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
import sys
from typing import Dict
from urllib.request import Request

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect

BUFFER_SIZE = 2**16  # 64KB

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


async def main(url: str) -> None:
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

            await asyncio.gather(stdin_to_websocket(stdin_reader, websocket),
                                 websocket_to_stdout(websocket, stdout_writer))
        finally:
            if old_settings:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  old_settings)


async def stdin_to_websocket(reader: asyncio.StreamReader,
                             websocket: ClientConnection):
    try:
        while True:
            # Read at most BUFFER_SIZE bytes, this not affect
            # responsiveness since it will return as soon as
            # there is at least one byte.
            # The BUFFER_SIZE is chosen to be large enough to improve
            # throughput.
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            await websocket.send(data)
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in stdin_to_websocket: {e}', file=sys.stderr)
    finally:
        await websocket.close()


async def websocket_to_stdout(websocket: ClientConnection,
                              writer: asyncio.StreamWriter):
    try:
        while True:
            message = await websocket.recv()
            writer.write(message)
            await writer.drain()
    except websockets.exceptions.ConnectionClosed:
        print('WebSocket connection closed', file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in websocket_to_stdout: {e}', file=sys.stderr)


if __name__ == '__main__':
    server_url = sys.argv[1].strip('/')
    if '://' not in server_url:
        # Keep backward compatibility for legacy server URLs without protocol
        # TODO(aylei): Remove this after 0.10.0
        server_url = f'http://{server_url}'

    server_proto, server_fqdn = server_url.split('://')
    websocket_proto = 'ws'
    if server_proto == 'https':
        websocket_proto = 'wss'
    server_url = f'{websocket_proto}://{server_fqdn}'
    websocket_url = (f'{server_url}/kubernetes-pod-ssh-proxy'
                     f'?cluster_name={sys.argv[2]}')
    asyncio.run(main(websocket_url))
