"""WebSocket proxy utilities for SSH tunneling."""

import asyncio
from enum import IntEnum
import struct
import time
from typing import Awaitable, Callable, Optional

import fastapi

from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

# How the session reached the SSH backend, for the `path` metric label. A
# plugin that redirects a session elsewhere reports its own value.
SSH_PATH_PORT_FORWARD = 'portforward'
SSH_PATH_SLURM = 'slurm'
SSH_PATH_REDIRECTED = 'redirected'

# Hook for plugins to inject SSH redirect logic. When set, it is called after
# WebSocket accept for clients that support the redirect protocol.
# TODO(aylei): support in slurm ssh handler
ssh_redirect_hook: Optional[Callable[[fastapi.WebSocket, str],
                                     Awaitable[Optional[dict]]]] = None


def register_ssh_redirect_hook(
    hook: Callable[[fastapi.WebSocket, str],
                   Awaitable[Optional[dict]]],) -> None:
    """Register a hook that checks whether an SSH connection should redirect.

    The hook is called with (websocket, cluster_name) after the WebSocket is
    accepted but before the backend connection is established.
    """
    global ssh_redirect_hook
    if ssh_redirect_hook is not None:
        raise ValueError(
            'SSH redirect hook already registered by '
            f'{ssh_redirect_hook.__module__}.{ssh_redirect_hook.__qualname__}')
    ssh_redirect_hook = hook


class SSHMessageType(IntEnum):
    REGULAR_DATA = 0
    PINGPONG = 1
    LATENCY_MEASUREMENT = 2
    REDIRECT = 3


class _BackendTurnaroundSampler:
    """Times the backend round trip by pairing a small write with a read.

    The SSH stream is opaque and must stay byte-exact, so we cannot inject a
    probe into it. But an interactive session is a request/response
    conversation: a keystroke goes out as one small frame and its echo comes
    back. Pairing on that shape measures everything past this process -- the
    port-forward tunnel, both sshd hops, the pty, the shell -- for the cost of
    two clock reads on a loop that already unpacks a header per frame.

    This is a distribution, not a per-keystroke truth. Backend traffic that
    answers no write (window adjusts, server keepalives, program output) can
    attach a read to the wrong write, so a sample is only taken when both of
    these hold:

    * the write is keystroke-sized (``_MAX_WRITE_BYTES``) -- a paste, an scp
      or a terminal resize burst is not a request/response exchange, and a
      large write also *clears* any outstanding stamp, because the next read
      is then far more likely to be its echo than the keystroke's;
    * the reply arrives within ``_MAX_PENDING_SECONDS`` -- past that it is
      far more likely to be unrelated output than a very slow echo, and
      including it would corrupt the tail we are trying to measure.

    Only the *first* read after a stamped write is paired; a long echo split
    across several reads contributes one sample, timed to first byte, which is
    what the user perceives.

    When typing pipelines -- a second keystroke sent before the first is
    answered -- the *oldest* outstanding stamp is kept rather than discarded.
    The stream is ordered, so the first read back is the first write's echo,
    which makes the oldest stamp the correct attribution. An earlier version
    dropped the sample instead, on the theory that pipelined frames are
    ambiguous; e2e testing showed that throws away precisely the samples
    worth having, because a stalled backend is exactly when a user keeps
    typing into the lag.

    Not thread-safe and does not need to be: both callers are coroutines on
    one event loop.
    """

    # Keystroke-sized. One keypress on a doubly-encrypted SSH stream is
    # ~60-120 bytes; 512 leaves room for escape sequences and small control
    # frames without admitting a paste.
    _MAX_WRITE_BYTES = 512
    _MAX_PENDING_SECONDS = 2.0

    def __init__(self, path: str) -> None:
        self._enabled = metrics_utils.METRICS_ENABLED
        self._histogram = None
        if self._enabled:
            self._histogram = (
                metrics_utils.SKY_APISERVER_SSH_BACKEND_TURNAROUND_SECONDS.
                labels(path=path))
        self._pending_at: Optional[float] = None

    def on_write(self, size: int) -> None:
        """Called just before handing ``size`` bytes to the backend."""
        if not self._enabled:
            return
        if size == 0 or size > self._MAX_WRITE_BYTES:
            # Not a request/response exchange. Drop any outstanding stamp too:
            # the next read is more likely to answer this bulk write than the
            # keystroke before it.
            self._pending_at = None
            return
        if self._pending_at is not None:
            # Pipelined typing. Keep the older stamp: the stream is ordered,
            # so the next read answers the earlier write.
            return
        self._pending_at = time.monotonic()

    def on_read(self) -> None:
        """Called as soon as bytes come back from the backend."""
        if not self._enabled:
            return
        pending_at = self._pending_at
        self._pending_at = None
        if pending_at is None:
            return
        elapsed = time.monotonic() - pending_at
        if elapsed > self._MAX_PENDING_SECONDS:
            return
        assert self._histogram is not None
        self._histogram.observe(elapsed)


async def run_websocket_proxy(
    websocket: fastapi.WebSocket,
    read_from_backend: Callable[[], Awaitable[bytes]],
    write_to_backend: Callable[[bytes], Awaitable[None]],
    close_backend: Callable[[], Awaitable[None]],
    timestamps_supported: bool,
    path: str = SSH_PATH_PORT_FORWARD,
) -> bool:
    """Run bidirectional WebSocket-to-backend proxy.

    Args:
        websocket: FastAPI WebSocket connection
        read_from_backend: Async callable to read bytes from backend
        write_to_backend: Async callable to write bytes to backend
        close_backend: Async callable to close backend connection
        timestamps_supported: Whether to use message type framing
        path: How this session reaches the backend, used as the `path` label
            on sky_apiserver_ssh_backend_turnaround_seconds. Callers that
            reach the pod some other way (a plugin connecting in-cluster,
            say) should pass their own value so the two are not averaged
            together.

    Returns:
        True if SSH failed, False otherwise
    """
    ssh_failed = False
    websocket_closed = False
    turnaround = _BackendTurnaroundSampler(path)

    async def websocket_to_backend():
        try:
            async for message in websocket.iter_bytes():
                if timestamps_supported:
                    type_size = struct.calcsize('!B')
                    message_type = struct.unpack('!B', message[:type_size])[0]
                    if message_type == SSHMessageType.REGULAR_DATA:
                        # Regular data - strip type byte and forward to backend
                        message = message[type_size:]
                    elif message_type == SSHMessageType.PINGPONG:
                        # PING message - respond with PONG
                        ping_id_size = struct.calcsize('!I')
                        if len(message) != type_size + ping_id_size:
                            raise ValueError(
                                f'Invalid PING message length: {len(message)}')
                        # Return the same PING message for latency measurement
                        await websocket.send_bytes(message)
                        continue
                    elif message_type == SSHMessageType.LATENCY_MEASUREMENT:
                        # Latency measurement from client
                        latency_size = struct.calcsize('!Q')
                        if len(message) != type_size + latency_size:
                            raise ValueError('Invalid latency measurement '
                                             f'message length: {len(message)}')
                        avg_latency_ms = struct.unpack(
                            '!Q',
                            message[type_size:type_size + latency_size])[0]
                        latency_seconds = avg_latency_ms / 1000
                        metrics_utils.SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS.observe(  # pylint: disable=line-too-long
                            latency_seconds)
                        continue
                    else:
                        raise ValueError(
                            f'Unknown message type: {message_type}')

                try:
                    turnaround.on_write(len(message))
                    await write_to_backend(message)
                except Exception as e:  # pylint: disable=broad-except
                    # Typically we will not reach here, if the conn to backend
                    # is disconnected, backend_to_websocket will exit first.
                    # But just in case.
                    logger.error(f'Failed to write to backend through '
                                 f'connection: {e}')
                    nonlocal ssh_failed
                    ssh_failed = True
                    break
        except fastapi.WebSocketDisconnect:
            pass
        nonlocal websocket_closed
        websocket_closed = True
        await close_backend()

    async def backend_to_websocket():
        try:
            while True:
                data = await read_from_backend()
                if data:
                    turnaround.on_read()
                if not data:
                    if not websocket_closed:
                        logger.warning(
                            'SSH connection to backend is disconnected '
                            'before websocket connection is closed')
                        nonlocal ssh_failed
                        ssh_failed = True
                    break
                if timestamps_supported:
                    # Prepend message type byte (0 = regular data)
                    message_type_bytes = struct.pack(
                        '!B', SSHMessageType.REGULAR_DATA.value)
                    data = message_type_bytes + data
                await websocket.send_bytes(data)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            await websocket.close()
        except Exception:  # pylint: disable=broad-except
            # The websocket might have been closed by the client
            pass

    await asyncio.gather(websocket_to_backend(),
                         backend_to_websocket(),
                         return_exceptions=True)

    return ssh_failed
