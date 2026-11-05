"""Regression tests for streaming read-timeout behavior in the API client.

Background: streaming reads (`/api/stream` log following, etc.) were issued with
no read timeout, so a streaming connection that silently stalls -- a proxy/CDN
drops the long-lived connection, or the peer goes away mid-stream without
closing -- left the client blocked in ``recv()`` forever. The server sends a
heartbeat every ``stream_utils._HEARTBEAT_INTERVAL`` seconds on these streams to
keep them busy; the client-side fix is a read timeout comfortably above that
interval, so a healthy stream never trips it but a dead one raises a (retryable)
error instead of hanging. See PR #9990.
"""
import socket
import threading
import time

import pytest
import requests

from sky.client import common as client_common
from sky.server import rest
from sky.server import stream_utils


class _SilentStreamServer:
    """Sends HTTP 200 + one chunk, then holds the socket open and silent.

    This mimics a dead/stalled stream: the response started, but no further
    bytes (not even a heartbeat) ever arrive and the peer never closes.
    """

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._conns = []  # hold accepted sockets open (never send more, never close)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._sock.settimeout(0.5)
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(5)
            data = b''
            try:
                while b'\r\n\r\n' not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                conn.sendall(b'HTTP/1.1 200 OK\r\n'
                             b'Content-Type: text/plain\r\n'
                             b'Transfer-Encoding: chunked\r\n\r\n'
                             b'3\r\nhi\n\r\n')  # one chunk, then silence
            except OSError:
                continue
            self._conns.append(conn)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/api/stream'

    def close(self):
        self._stop = True
        for c in self._conns:
            try:
                c.close()  # unblock any client stuck reading this socket
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def silent_stream_server(monkeypatch):
    # Make sure localhost is not routed through any ambient HTTP proxy, which
    # would otherwise intercept the connection and mask the behavior.
    for var in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY',
                'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('NO_PROXY', '*')
    server = _SilentStreamServer()
    try:
        yield server
    finally:
        server.close()


def _consume_stream(url, read_timeout):
    resp = rest._session.request('GET',
                                 url,
                                 stream=True,
                                 timeout=(5, read_timeout))
    for _ in resp.iter_lines():
        pass


def test_streaming_read_timeout_fires_on_silent_stream(silent_stream_server):
    """A read timeout detects a silently-stalled stream promptly (raises a
    retryable RequestException) instead of blocking forever."""
    read_timeout = 2
    start = time.monotonic()
    with pytest.raises(requests.exceptions.RequestException) as exc_info:
        _consume_stream(silent_stream_server.url, read_timeout)
    elapsed = time.monotonic() - start
    assert elapsed < read_timeout + 5, f'raised too late: {elapsed:.1f}s'
    # Surfaces as a timeout (a read timeout during streaming iteration is
    # reported as ConnectionError("Read timed out"), still a RequestException).
    assert 'timed out' in str(exc_info.value).lower()


def test_no_read_timeout_blocks_forever(silent_stream_server):
    """Without a read timeout the same silent stream never returns within a
    generous window -- the hang this fix guards against."""
    done = threading.Event()

    def run():
        try:
            _consume_stream(silent_stream_server.url, None)
        except Exception:  # pylint: disable=broad-except
            pass
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    # The read-timeout case above returns in ~2s; still blocked at 4s => hang.
    assert not done.wait(timeout=4), 'stream returned without a read timeout'


def test_stream_read_timeout_exceeds_server_heartbeat():
    """Design invariant: the client streaming read timeout must stay above the
    server's stream heartbeat interval, so a healthy (idle-but-heartbeating)
    stream never trips it."""
    assert (client_common.API_SERVER_REQUEST_STREAM_READ_TIMEOUT_SECONDS >
            stream_utils._HEARTBEAT_INTERVAL)
