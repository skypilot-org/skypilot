"""Utilities for server-side interactive SSH functionality."""
import os
import socket


def get_socket_path(session_id: str) -> str:
    """Get the Unix socket path for interactive SSH session input."""
    return f'/tmp/sky_interactive_{session_id}.sock'


def create_socket_server(session_id: str,
                         timeout: float = 0.5) -> socket.socket:
    """Create and bind a Unix socket server for receiving interactive input."""
    socket_path = get_socket_path(session_id)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    sock_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_server.bind(socket_path)
    sock_server.listen(1)
    sock_server.settimeout(timeout)
    return sock_server


def send_to_socket(session_id: str, data: str, timeout: float = 5.0) -> None:
    """Send user input data to the Unix socket for an interactive session.

    Args:
        session_id: The unique session identifier.
        data: The data to send.
        timeout: Connection timeout in seconds.

    Raises:
        OSError: If the socket connection fails.
    """
    socket_path = get_socket_path(session_id)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path)
    # Send input with newline
    sock.send((data + '\n').encode())
    sock.close()
