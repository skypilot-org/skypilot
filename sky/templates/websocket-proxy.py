#!/usr/bin/env python3
import asyncio
import sys
import websockets
import os


async def main(websocket_url):
    print(f'Connecting to {websocket_url}', file=sys.stderr)
    async with websockets.connect(websocket_url, ping_interval=None, close_timeout=60) as websocket:
        print("WebSocket connection established", file=sys.stderr)

        if os.isatty(sys.stdin.fileno()):
            import tty
            import termios
            old_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
        else:
            old_settings = None

        try:
            await asyncio.gather(stdin_to_websocket(websocket), websocket_to_stdout(websocket))
        finally:
            if old_settings:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)


async def stdin_to_websocket(websocket):
    try:
        while True:
            data = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.buffer.read, 1)
            if not data:
                break
            await websocket.send(data)
    except Exception as e:
        print(f"Error in stdin_to_websocket: {e}", file=sys.stderr)
    finally:
        await websocket.close()


async def websocket_to_stdout(websocket):
    try:
        while True:
            message = await websocket.recv()
            sys.stdout.buffer.write(message)
            await asyncio.get_event_loop().run_in_executor(None, sys.stdout.buffer.flush)
    except websockets.exceptions.ConnectionClosed:
        print("WebSocket connection closed", file=sys.stderr)
    except Exception as e:
        print(f"Error in websocket_to_stdout: {e}", file=sys.stderr)


if __name__ == '__main__':
    websocket_url = f'ws://{sys.argv[1]}/kubernetes-pod-ssh-proxy?pod_name={sys.argv[4]}&namespace={sys.argv[3]}&context={sys.argv[2]}'
    asyncio.run(main(websocket_url))
