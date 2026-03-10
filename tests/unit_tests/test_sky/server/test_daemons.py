"""Tests for sky.server.daemons."""
import os
import sys
import tempfile

from sky import skypilot_config
from sky.server import daemons


def _mock_get_nested(max_bytes):
    """Return a patched get_nested that overrides daemon_log_max_bytes."""
    original = skypilot_config.get_nested

    def patched(keys, default=None):
        if keys == ('api_server', 'daemon_log_max_bytes'):
            return max_bytes
        return original(keys, default)

    return patched


class TestDaemonLogRotation:
    """Tests for daemon log rotation."""

    def _redirect_stdout_stderr(self, fd: int):
        """Redirect stdout and stderr to the given fd via dup2."""
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())

    def test_rotates_when_exceeds_threshold(self, monkeypatch):
        """Log is backed up to .log.1 and truncated when exceeding threshold."""
        threshold = 1024  # 1 KB for testing
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                # Open with O_APPEND to mimic executor.py behavior.
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data exceeding the threshold.
                data = b'x' * (threshold + 100)
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()
                assert os.fstat(sys.stdout.fileno()).st_size == threshold + 100

                # Rotation should happen.
                daemons._rotate_daemon_log(tmp_path)
                assert os.fstat(sys.stdout.fileno()).st_size == 0

                # Backup should contain the original data.
                with open(backup_path, 'rb') as check:
                    assert check.read() == data

                # Writes after rotation should start from position 0
                # (no sparse hole).
                msg = b'hello after rotation\n'
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
            if os.path.exists(backup_path):
                os.unlink(backup_path)

    def test_old_backup_replaced_on_next_rotation(self, monkeypatch):
        """Old .log.1 backup is replaced on subsequent rotation."""
        threshold = 1024
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # First rotation.
                first_data = b'A' * (threshold + 100)
                os.write(sys.stdout.fileno(), first_data)
                sys.stdout.flush()
                daemons._rotate_daemon_log(tmp_path)
                with open(backup_path, 'rb') as check:
                    assert check.read() == first_data

                # Write new data exceeding threshold again.
                second_data = b'B' * (threshold + 200)
                os.write(sys.stdout.fileno(), second_data)
                sys.stdout.flush()
                daemons._rotate_daemon_log(tmp_path)

                # Backup should now contain second data, not first.
                with open(backup_path, 'rb') as check:
                    assert check.read() == second_data

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)
            if os.path.exists(backup_path):
                os.unlink(backup_path)

    def test_no_rotation_when_under_threshold(self, monkeypatch):
        """No rotation or backup when log size is under threshold."""
        threshold = 1024
        monkeypatch.setattr(skypilot_config, 'get_nested',
                            _mock_get_nested(threshold))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data under the threshold.
                data = b'x' * (threshold - 100)
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

                daemons._rotate_daemon_log(tmp_path)

                # File should not be truncated.
                assert os.fstat(sys.stdout.fileno()).st_size == threshold - 100
                # No backup should be created.
                assert not os.path.exists(backup_path)

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)

    def test_rotation_disabled_when_max_bytes_zero(self, monkeypatch):
        """Rotation is disabled when max_bytes is set to 0."""
        monkeypatch.setattr(skypilot_config, 'get_nested', _mock_get_nested(0))

        saved_stdout_fd = os.dup(sys.stdout.fileno())
        saved_stderr_fd = os.dup(sys.stderr.fileno())
        try:
            with tempfile.NamedTemporaryFile(mode='ab',
                                             delete=False,
                                             suffix='.log') as f:
                tmp_path = f.name
                backup_path = tmp_path + '.1'
                append_fd = os.open(tmp_path,
                                    os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                self._redirect_stdout_stderr(append_fd)

                # Write data that would normally exceed a threshold.
                data = b'x' * 2048
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

                daemons._rotate_daemon_log(tmp_path)

                # File should not be truncated.
                assert os.fstat(sys.stdout.fileno()).st_size == 2048
                # No backup should be created.
                assert not os.path.exists(backup_path)

                os.close(append_fd)
        finally:
            os.dup2(saved_stdout_fd, sys.stdout.fileno())
            os.dup2(saved_stderr_fd, sys.stderr.fileno())
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.unlink(tmp_path)
