"""Utilities for server-side interactive SSH functionality."""
import os
import socket


def get_socket_path(session_id: str) -> str:
    """Get the Unix socket path for interactive SSH session input.

    Args:
        session_id: The unique session identifier.

    Returns:
        The socket path: /tmp/sky_interactive_{session_id}.sock
    """
    return f'/tmp/sky_interactive_{session_id}.sock'


def create_socket_server(session_id: str,
                         timeout: float = 0.5) -> socket.socket:
    """Create and bind a Unix socket server for receiving interactive input.

    Args:
        session_id: The unique session identifier.
        timeout: Socket timeout in seconds for accept() operations.

    Returns:
        A bound and listening socket server ready to accept connections.
    """
    socket_path = get_socket_path(session_id)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    sock_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_server.bind(socket_path)
    sock_server.listen(1)
    sock_server.settimeout(timeout)
    return sock_server


def send_to_socket(session_id: str, data: str, timeout: float = 5.0) -> None:
    """Send data to the interactive socket for a session.

    Args:
        session_id: The unique session identifier.
        data: The data to send (a newline will be appended automatically).
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
