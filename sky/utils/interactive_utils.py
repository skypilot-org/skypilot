"""Utilities for server-side interactive SSH functionality."""
import array
import socket


def get_pty_socket_path(session_id: str) -> str:
    """Get the Unix socket path for PTY file descriptor passing."""
    return f'/tmp/sky_pty_{session_id}.sock'


def send_fd(sock: socket.socket, fd: int) -> None:
    """Send file descriptor via Unix socket using SCM_RIGHTS.

    SCM_RIGHTS allows us to send or receive a set of open
    file descriptors from another process.

    See:
    https://man7.org/linux/man-pages/man7/unix.7.html
    https://man7.org/linux/man-pages/man3/cmsg.3.html

    Args:
        sock: Connected Unix socket.
        fd: File descriptor to send.
    """
    sock.sendmsg(
        [b'x'],  # Dummy data
        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])


def recv_fd(sock: socket.socket) -> int:
    """Receive file descriptor via Unix socket using SCM_RIGHTS.

    Args:
        sock: Connected Unix socket.

    Returns:
        Received file descriptor.

    Raises:
        RuntimeError: If no file descriptor was received.
    """
    # NOTE: recvmsg() has no async equivalent
    _, ancdata, _, _ = sock.recvmsg(
        1, socket.CMSG_SPACE(array.array('i', [0]).itemsize))
    if not ancdata:
        raise RuntimeError('No file descriptor received - '
                           'sender may have closed connection')
    _, _, cmsg_data = ancdata[0]
    return array.array('i', cmsg_data)[0]
