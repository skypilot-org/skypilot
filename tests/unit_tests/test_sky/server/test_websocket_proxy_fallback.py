"""The SSH proxy client falls back to the API server when a redirect fails.

Drives the real client script as a subprocess, the way ssh's ProxyCommand
does, against local stand-ins for the API server and the redirect target.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import threading
from typing import List, Optional

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from sky.server.websocket_utils import SSHMessageType

_SCRIPT = (pathlib.Path(__file__).resolve().parents[4] / 'sky' / 'templates' /
           'websocket_proxy.py')
_REPO = _SCRIPT.parents[2]
_DATA = bytes([SSHMessageType.REGULAR_DATA.value])
# ssh writes its version banner first, before hearing anything.
_CLIENT_BANNER = b'SSH-2.0-client\r\n'
_FALLBACK_BANNER = b'SSH-2.0-api-server\r\n'
_TARGET_BANNER = b'SSH-2.0-agent\r\n'


class _Servers:
    """A stand-in API server and redirect target on a private event loop."""

    def __init__(self, target_mode: str) -> None:
        self.target_mode = target_mode
        self.fallback_received: List[bytes] = []
        self.target_received: List[bytes] = []
        self.api_port = 0
        self.target_port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: Optional[asyncio.Future] = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> '_Servers':
        self._thread.start()
        assert self._ready.wait(10)
        return self

    def __exit__(self, *exc) -> None:
        self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(10)

    def _run(self) -> None:
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop = self._loop.create_future()
        refuse = (lambda conn, req: Response(403, 'Forbidden', Headers(), b'')
                  if self.target_mode == 'refuse' else None)
        async with serve(self._api, '127.0.0.1', 0) as api, \
                serve(self._target, '127.0.0.1', 0,
                      process_request=refuse) as target:
            self.api_port = api.sockets[0].getsockname()[1]
            self.target_port = target.sockets[0].getsockname()[1]
            if self.target_mode == 'unreachable':
                target.close()
                await target.wait_closed()
            self._ready.set()
            await self._stop

    async def _api(self, ws) -> None:
        if 'no_redirect=1' in ws.request.path:
            # Serving the session itself: the client's banner must arrive
            # intact, even though a failed target already consumed it.
            self.fallback_received.append((await ws.recv())[1:])
            await ws.send(_DATA + _FALLBACK_BANNER)
            await ws.close()
            return
        target = f'ws://127.0.0.1:{self.target_port}/kubernetes-pod-ssh-proxy'
        await ws.send(
            bytes([SSHMessageType.REDIRECT.value]) + json.dumps({
                'url': target,
                'headers': {}
            }).encode())
        await ws.wait_closed()

    async def _target(self, ws) -> None:
        # Take the client's banner first: that is what makes a fallback
        # have to replay it.
        self.target_received.append((await ws.recv())[1:])
        if self.target_mode == 'serve':
            await ws.send(_DATA + _TARGET_BANNER)
        elif self.target_mode == 'silent':
            await ws.wait_closed()
            return
        await ws.close(1011)


def _run_client(api_port: int,
                expect: bytes,
                timeout: float = 30,
                script: pathlib.Path = _SCRIPT) -> bytes:
    """Run the proxy as ssh does: write its banner, keep stdin open until the
    server's banner arrives, then close stdin and let the proxy exit.

    "Hung" means the expected bytes never arrived -- the proxy was waiting on
    a dead socket, and so was ssh.
    """
    env = dict(os.environ, PYTHONPATH=str(_REPO))
    env.pop('SKYPILOT_CONFIG', None)
    # Debug logging goes to stdout, which is the SSH stream here.
    env.pop('SKYPILOT_DEBUG', None)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script), f'http://127.0.0.1:{api_port}', 'c',
            'kubernetes-pod-ssh-proxy'
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    watchdog = threading.Timer(timeout, proc.kill)
    watchdog.start()
    try:
        proc.stdin.write(_CLIENT_BANNER)
        proc.stdin.flush()
        out = proc.stdout.read(len(expect))
        if len(out) < len(expect):
            pytest.fail(f'ssh proxy hung instead of serving; got {out!r}')
        proc.stdin.close()
        proc.wait(timeout)
        return out
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()


@pytest.mark.parametrize('mode', ['accept_then_close', 'refuse', 'unreachable'])
def test_a_failed_redirect_falls_back_and_replays_what_ssh_sent(
        tmp_path, monkeypatch, mode):
    """accept_then_close is the case that hung: an agent that may not read
    the target pod accepts the socket, then closes it."""
    monkeypatch.setenv('HOME', str(tmp_path))
    with _Servers(mode) as servers:
        out = _run_client(servers.api_port, _FALLBACK_BANNER)
    assert out == _FALLBACK_BANNER
    assert servers.fallback_received == [_CLIENT_BANNER]


def test_a_silent_redirect_target_falls_back(tmp_path, monkeypatch):
    """A wedged target accepts and never sends or closes."""
    monkeypatch.setenv('HOME', str(tmp_path))
    source = _SCRIPT.read_text()
    assert 'FIRST_DATA_TIMEOUT_SECONDS = 30\n' in source
    script = tmp_path / 'websocket_proxy.py'
    script.write_text(
        source.replace('FIRST_DATA_TIMEOUT_SECONDS = 30\n',
                       'FIRST_DATA_TIMEOUT_SECONDS = 1\n'))
    with _Servers('silent') as servers:
        out = _run_client(servers.api_port, _FALLBACK_BANNER, script=script)
    assert out == _FALLBACK_BANNER
    assert servers.fallback_received == [_CLIENT_BANNER]


def test_a_redirect_target_that_serves_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    with _Servers('serve') as servers:
        out = _run_client(servers.api_port, _TARGET_BANNER)
    assert out == _TARGET_BANNER
    assert servers.target_received == [_CLIENT_BANNER]
    assert not servers.fallback_received
