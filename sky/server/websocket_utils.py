"""WebSocket proxy utilities for SSH tunneling."""

import asyncio
from enum import IntEnum
import os
import struct
from typing import Awaitable, Callable, Optional

import fastapi

from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

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


async def run_websocket_proxy(
    websocket: fastapi.WebSocket,
    read_from_backend: Callable[[], Awaitable[bytes]],
    write_to_backend: Callable[[bytes], Awaitable[None]],
    close_backend: Callable[[], Awaitable[None]],
    timestamps_supported: bool,
) -> bool:
    """Run bidirectional WebSocket-to-backend proxy.

    Args:
        websocket: FastAPI WebSocket connection
        read_from_backend: Async callable to read bytes from backend
        write_to_backend: Async callable to write bytes to backend
        close_backend: Async callable to close backend connection
        timestamps_supported: Whether to use message type framing

    Returns:
        True if SSH failed, False otherwise
    """
    ssh_failed = False
    websocket_closed = False

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
                        metrics_utils.SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS.labels(  # pylint: disable=line-too-long
                            pid=os.getpid()).observe(latency_seconds)
                        continue
                    else:
                        raise ValueError(
                            f'Unknown message type: {message_type}')

                try:
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
