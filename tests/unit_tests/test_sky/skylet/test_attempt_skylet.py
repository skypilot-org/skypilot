"""Unit tests for attempt_skylet module."""
import importlib
import signal
from unittest import mock

import pytest

from sky.skylet import constants


@pytest.fixture
def skylet_env(tmp_path, monkeypatch):
    """Shared fixture for skylet tests with isolated runtime directory."""
    runtime_dir = tmp_path
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(runtime_dir))

    sky_dir = runtime_dir / '.sky'
    sky_dir.mkdir(parents=True, exist_ok=True)

    return {
        'runtime_dir': runtime_dir,
        'pid_file': sky_dir / 'skylet_pid',
        'port_file': sky_dir / 'skylet_port',
        'version_file': sky_dir / 'skylet_version',
        'log_file': sky_dir / 'skylet.log',
    }


class TestAttemptSkyletRunningCheck:
    """Test running check logic (PID file and grep fallback)."""

    def test_pid_file_process_alive(self, skylet_env, monkeypatch):
        """PID file exists + process alive -> running=True."""
        pid = 12345
        skylet_env['pid_file'].write_text(str(pid))

        kill_calls = []

        def mock_kill(pid, sig):
            kill_calls.append((pid, sig))

        monkeypatch.setattr('os.kill', mock_kill)
        monkeypatch.setattr('subprocess.run', mock.Mock())

        from sky.skylet import attempt_skylet
        importlib.reload(attempt_skylet)

        assert attempt_skylet.running is True
        assert (pid, 0) in kill_calls

    def test_pid_file_process_dead(self, skylet_env, monkeypatch):
        """PID file exists + process dead -> running=False."""
        skylet_env['pid_file'].write_text('12345')

        def mock_kill(pid, sig):
            if sig == 0:
                raise ProcessLookupError()

        monkeypatch.setattr('os.kill', mock_kill)
        monkeypatch.setattr('subprocess.run', mock.Mock())

        from sky.skylet import attempt_skylet
        importlib.reload(attempt_skylet)

        assert attempt_skylet.running is False

    def test_pid_file_invalid_content_raises_error(self, skylet_env):
        """PID file with invalid content raises RuntimeError on restart."""
        skylet_env['pid_file'].write_text('not_a_pid')

        with pytest.raises(RuntimeError) as exc_info:
            from sky.skylet import attempt_skylet
            importlib.reload(attempt_skylet)

        assert 'Failed to read PID file' in str(exc_info.value)

    def test_no_pid_file_grep_fallback(self, skylet_env, monkeypatch):
        """No PID file -> falls back to grep-based check."""
        assert not skylet_env['pid_file'].exists()

        # returncode=0 means grep found a process
        mock_result = mock.Mock(returncode=0)
        monkeypatch.setattr('subprocess.run', lambda *a, **kw: mock_result)

        from sky.skylet import attempt_skylet
        importlib.reload(attempt_skylet)

        assert attempt_skylet.running is True


class TestRestartSkylet:
    """Test restart_skylet() function."""

    @pytest.fixture(autouse=True)
    def setup(self, skylet_env):
        """Get restart_skylet function with correct runtime dir."""
        self.env = skylet_env

        from sky.skylet import attempt_skylet
        importlib.reload(attempt_skylet)
        self.restart_skylet = attempt_skylet.restart_skylet

    def test_handles_dead_process_gracefully(self, monkeypatch):
        """restart_skylet() doesn't crash if process already dead."""
        self.env['pid_file'].write_text('99999')

        monkeypatch.setattr(
            'os.kill', lambda p, s: (_ for _ in ()).throw(ProcessLookupError()))
        mock_run = mock.Mock(return_value=mock.Mock(returncode=0))
        monkeypatch.setattr('subprocess.run', mock_run)
        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda x: 46590)

        self.restart_skylet()
        assert mock_run.called

    def test_raises_error_on_pid_file_read_failure(self, monkeypatch):
        """restart_skylet() raises RuntimeError on PID file read failure."""
        self.env['pid_file'].write_text('99999')

        original_open = open

        def mock_open(file, *args, **kwargs):
            if str(file).endswith('skylet_pid') and 'r' in args[0]:
                raise IOError('Disk read error')
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr('builtins.open', mock_open)

        with pytest.raises(RuntimeError) as exc_info:
            self.restart_skylet()
        assert 'Failed to read PID file' in str(exc_info.value)

    def test_complete_flow_with_pid_file(self, monkeypatch):
        """Complete flow: kill by PID, start new skylet, write all files."""
        old_pid = 88888
        self.env['pid_file'].write_text(str(old_pid))

        killed_pids = []
        monkeypatch.setattr('os.kill', lambda p, s: killed_pids.append((p, s)))

        subprocess_calls = []

        def mock_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            return mock.Mock(returncode=0)

        monkeypatch.setattr('subprocess.run', mock_run)

        test_port = 46593
        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda x: test_port)

        self.restart_skylet()

        assert len(subprocess_calls) == 1

        # Killed old process by PID
        assert (old_pid, signal.SIGKILL) in killed_pids

        # Started new skylet with correct port and PID capture
        nohup_cmd = subprocess_calls[0]
        assert f'--port={test_port}' in nohup_cmd
        assert 'echo $!' in nohup_cmd
        assert str(self.env['pid_file']) in nohup_cmd
        assert str(self.env['log_file']) in nohup_cmd

        # Wrote port and version files
        assert self.env['port_file'].read_text() == str(test_port)
        assert self.env['version_file'].read_text() == constants.SKYLET_VERSION

    def test_complete_flow_without_pid_file(self, monkeypatch):
        """Complete flow: grep fallback kill, start new skylet, write all files."""
        if self.env['pid_file'].exists():
            self.env['pid_file'].unlink()

        subprocess_calls = []

        def mock_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            return mock.Mock(returncode=0)

        monkeypatch.setattr('subprocess.run', mock_run)

        test_port = 46594
        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda x: test_port)

        self.restart_skylet()

        # 2 calls: grep kill + nohup
        assert len(subprocess_calls) == 2

        # Used grep-based kill fallback
        grep_cmd = subprocess_calls[0]
        assert 'grep "sky.skylet.skylet"' in grep_cmd
        assert 'xargs kill' in grep_cmd

        nohup_cmd = subprocess_calls[1]
        assert f'--port={test_port}' in nohup_cmd

        # Wrote files
        assert self.env['port_file'].read_text() == str(test_port)
        assert self.env['version_file'].read_text() == constants.SKYLET_VERSION
