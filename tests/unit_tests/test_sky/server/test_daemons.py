"""Tests for sky.server.daemons."""
import os
import sys
import tempfile

from sky.server import constants as server_constants
from sky.server import daemons


class TestDaemonLogTruncate:
    """Tests for daemon log truncation."""

    def _redirect_stdout_stderr(self, fd: int):
        """Redirect stdout and stderr to the given fd via dup2."""
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())

    def test_truncates_when_exceeds_threshold(self, monkeypatch):
        """File is truncated to 0 when it exceeds the size threshold."""
        threshold = 1024  # 1 KB for testing
        monkeypatch.setattr(server_constants, 'DAEMON_LOG_MAX_BYTES', threshold)

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab', delete=False) as f:
                tmp_path = f.name
                # Open with O_APPEND to mimic executor.py behavior.
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data exceeding the threshold.
                data = b'x' * (threshold + 100)
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()
                assert os.fstat(sys.stdout.fileno()).st_size == threshold + 100

                # Truncation should happen.
                daemons._maybe_truncate_daemon_log()
                assert os.fstat(sys.stdout.fileno()).st_size == 0

                # Writes after truncation should start from position 0
                # (no sparse hole).
                msg = b'hello after truncation\n'
                os.write(sys.stdout.fileno(), msg)
                sys.stdout.flush()
                assert os.fstat(sys.stdout.fileno()).st_size == len(msg)

                # Verify the content on disk.
                with open(tmp_path, 'rb') as check:
                    assert check.read() == msg

                os.close(append_fd)
        finally:
            # Restore original stdout/stderr.
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)
