"""Tests for file descriptor passing used for interactive SSH auth."""
import os
import socket
import tempfile

import pytest

from sky.utils import interactive_utils


def test_send_fd_and_recv_fd_end_to_end():
    """Test that send_fd and recv_fd work together correctly."""
    # Create a socket pair for testing
    sock1, sock2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM, 0)

    try:
        # Create a temporary file to get a real file descriptor
        # Keep it open during send (required for FD passing)
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = tmp_file.name
        fd_to_send = tmp_file.fileno()

        # Send the FD (must keep tmp_file open)
        interactive_utils.send_fd(sock1, fd_to_send)

        # Receive the FD
        received_fd = interactive_utils.recv_fd(sock2)

        # Verify we got a valid FD
        assert received_fd is not None
        assert isinstance(received_fd, int)
        assert received_fd >= 0

        # On my machine, it looks something like:
        # % lsof -p ...
        # COMMAND     PID  USER   FD   TYPE             DEVICE SIZE/OFF                NODE NAME
        # ...
        # python3.1 17878 kevin   11u   REG               1,18        0            66779483 /private/var/folders/y5/s2n482r93zv0rlrxdl9g9ndw0000gn/T/tmpeas5oyxt -> fd_to_send
        # python3.1 17878 kevin   12u   REG               1,18        0            66779483 /private/var/folders/y5/s2n482r93zv0rlrxdl9g9ndw0000gn/T/tmpeas5oyxt -> received_fd

        # Verify the received FD works (can write to it)
        test_data = b'test'
        os.write(received_fd, test_data)
        os.close(received_fd)
        tmp_file.close()

        # Verify the data written to received_fd appears in the file
        with open(tmp_path, 'rb') as f:
            assert f.read() == test_data

    finally:
        sock1.close()
        sock2.close()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
