#!/usr/bin/env python3
"""Starting a websocket with SkyPilot API server to proxy SSH to a k8s pod.

This script is useful for users who do not have local Kubernetes credentials.
"""
import asyncio
import os
import sys

import websockets
from websockets.asyncio.client import connect


async def main(url: str) -> None:
    async with connect(url, ping_interval=None) as websocket:
        if os.isatty(sys.stdin.fileno()):
            # pylint: disable=import-outside-toplevel
            import termios
            import tty
            old_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
        else:
            old_settings = None

        try:
            await asyncio.gather(stdin_to_websocket(websocket),
                                 websocket_to_stdout(websocket))
        finally:
            if old_settings:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  old_settings)


async def stdin_to_websocket(websocket):
    try:
        while True:
            data = await asyncio.get_event_loop().run_in_executor(
                None, sys.stdin.buffer.read, 1)
            if not data:
                break
            await websocket.send(data)
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in stdin_to_websocket: {e}', file=sys.stderr)
    finally:
        await websocket.close()


async def websocket_to_stdout(websocket):
    try:
        while True:
            message = await websocket.recv()
            sys.stdout.buffer.write(message)
            await asyncio.get_event_loop().run_in_executor(
                None, sys.stdout.buffer.flush)
    except websockets.exceptions.ConnectionClosed:
        print('WebSocket connection closed', file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error in websocket_to_stdout: {e}', file=sys.stderr)


if __name__ == '__main__':
    server_url = sys.argv[1].strip('/')
    if '://' in server_url:
        server_proto, server_fqdn = server_url.split('://')
        websocket_proto = 'ws'
        if server_proto == 'https':
            websocket_proto = 'wss'
        server_url = f'{websocket_proto}://{server_fqdn}'
    else:
        # Keep backward compatibility for legacy server URLs without protocol
        # TODO(aylei): Remove this after 0.10.0
        server_url = f'ws://{server_url}'
    websocket_url = (
        f'{server_url}/kubernetes-pod-ssh-proxy'
        f'?cluster_name={sys.argv[2]}')
    asyncio.run(main(websocket_url))
