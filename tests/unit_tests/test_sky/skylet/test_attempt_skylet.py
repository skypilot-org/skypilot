"""Unit tests for attempt_skylet module."""
import signal
from unittest import mock

import psutil
import pytest

from sky.skylet import attempt_skylet
from sky.skylet import constants


@pytest.fixture
def skylet_env(tmp_path, monkeypatch):
    """Shared fixture for skylet tests with isolated runtime directory."""
    sky_dir = tmp_path / '.sky'
    sky_dir.mkdir(parents=True, exist_ok=True)

    env = {
        'pid_file': sky_dir / 'skylet_pid',
        'port_file': sky_dir / 'skylet_port',
        'version_file': sky_dir / 'skylet_version',
        'log_file': sky_dir / 'skylet.log',
    }

    # Patch module-level path variables directly
    monkeypatch.setattr(attempt_skylet, 'PID_FILE', str(env['pid_file']))
    monkeypatch.setattr(attempt_skylet, 'PORT_FILE', str(env['port_file']))
    monkeypatch.setattr(attempt_skylet, 'VERSION_FILE',
                        str(env['version_file']))
    monkeypatch.setattr(attempt_skylet, 'SKYLET_LOG_FILE', str(env['log_file']))

    return env


class TestRunningCheck:
    """Test running check logic (PID file and grep fallback)."""

    def test_pid_file_process_alive(self, skylet_env, monkeypatch):
        """PID file exists + process alive -> running=True."""
        pid = 12345
        skylet_env['pid_file'].write_text(str(pid))

        # Mock psutil.Process to simulate a running skylet process
        mock_process = mock.Mock()
        mock_process.is_running.return_value = True
        mock_process.cmdline.return_value = [
            'python', '-m', 'sky.skylet.skylet', '--port=46590'
        ]
        monkeypatch.setattr('psutil.Process', lambda p: mock_process)

        assert attempt_skylet._find_running_skylet_pids() == [pid]

    def test_pid_file_process_dead(self, skylet_env, monkeypatch):
        """PID file exists + process dead -> running=False."""
        skylet_env['pid_file'].write_text('12345')

        # Mock psutil.Process to simulate dead process
        def mock_process_factory(pid):
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr('psutil.Process', mock_process_factory)

        assert attempt_skylet._find_running_skylet_pids() == []

    def test_no_pid_file_grep_fallback(self, skylet_env, monkeypatch):
        """No PID file -> falls back to grep-based check."""
        assert not skylet_env['pid_file'].exists()

        # Mock subprocess.run with proper stdout for grep command output
        # This simulates ps aux | grep ... output with PID 7680
        mock_result = mock.Mock(
            returncode=0,
            stdout=
            'sky         7680  0.0  0.0 1676360 153600 ?      Sl   09:30   0:16 /home/sky/skypilot-runtime/bin/python -m sky.skylet.skylet\n'
        )
        monkeypatch.setattr('subprocess.run', lambda *a, **kw: mock_result)

        assert attempt_skylet._find_running_skylet_pids() == [7680]


class TestVersionMatch:
    """Test _check_version_match logic."""

    def test_version_match_when_file_matches(self, skylet_env):
        """Version file with matching version -> _check_version_match returns (True, version)."""
        skylet_env['version_file'].write_text(constants.SKYLET_VERSION)

        match, version = attempt_skylet._check_version_match()
        assert match is True
        assert version == constants.SKYLET_VERSION

    def test_version_mismatch_when_file_stale(self, skylet_env):
        """Version file with stale version -> _check_version_match returns (False, old_version)."""
        skylet_env['version_file'].write_text('old_version')

        match, version = attempt_skylet._check_version_match()
        assert match is False
        assert version == 'old_version'

    def test_version_match_no_file(self, skylet_env):
        """No version file -> _check_version_match returns (False, None)."""
        assert not skylet_env['version_file'].exists()

        match, version = attempt_skylet._check_version_match()
        assert match is False
        assert version is None


class TestRestartSkylet:
    """Test restart_skylet() function."""

    @pytest.fixture(autouse=True)
    def setup(self, skylet_env, monkeypatch):
        """Get restart_skylet function with correct runtime dir."""
        self.env = skylet_env
        self.restart_skylet = attempt_skylet.restart_skylet

    def test_handles_dead_process_gracefully(self, monkeypatch):
        """restart_skylet() doesn't crash if process already dead."""
        self.env['pid_file'].write_text('99999')

        # Mock psutil.Process to simulate dead process
        def mock_process_factory(pid):
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr('psutil.Process', mock_process_factory)

        monkeypatch.setattr('os.kill', lambda p, s: None)
        mock_run = mock.Mock(return_value=mock.Mock(returncode=0))
        monkeypatch.setattr('subprocess.run', mock_run)

        self.restart_skylet()
        assert mock_run.called

    def test_complete_flow_with_pid_file(self, monkeypatch):
        """Complete flow: kill by PID, start new skylet, write all files."""
        old_pid = 88888
        self.env['pid_file'].write_text(str(old_pid))

        # Mock psutil.Process to simulate a running skylet process
        mock_process = mock.Mock()
        mock_process.is_running.return_value = True
        mock_process.cmdline.return_value = [
            'python', '-m', 'sky.skylet.skylet', '--port=46590'
        ]
        monkeypatch.setattr('psutil.Process', lambda p: mock_process)

        killed_pids = []
        monkeypatch.setattr('os.kill', lambda p, s: killed_pids.append((p, s)))

        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda port: constants.SKYLET_GRPC_PORT)

        subprocess_calls = []

        def mock_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            return mock.Mock(returncode=0)

        monkeypatch.setattr('subprocess.run', mock_run)

        self.restart_skylet()

        assert len(subprocess_calls) == 1

        # Killed old process by PID
        assert (old_pid, signal.SIGKILL) in killed_pids

        # Started new skylet with hardcoded port
        nohup_cmd = subprocess_calls[0]
        assert f'--port={constants.SKYLET_GRPC_PORT}' in nohup_cmd
        assert 'echo $!' in nohup_cmd
        assert str(self.env['pid_file']) in nohup_cmd
        assert str(self.env['log_file']) in nohup_cmd

        # Wrote port and version files
        assert self.env['port_file'].read_text() == str(
            constants.SKYLET_GRPC_PORT)
        assert self.env['version_file'].read_text() == constants.SKYLET_VERSION

    def test_complete_flow_without_pid_file(self, monkeypatch):
        """Complete flow: grep fallback kill, start new skylet, write all files."""
        if self.env['pid_file'].exists():
            self.env['pid_file'].unlink()

        killed_pids = []
        monkeypatch.setattr('os.kill', lambda p, s: killed_pids.append((p, s)))

        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda port: constants.SKYLET_GRPC_PORT)

        subprocess_calls = []

        def mock_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            # Mock subprocess.run with proper stdout for grep command output
            # Simulates ps aux output with multiple skylet processes
            if 'grep' in cmd:
                mock_result = mock.Mock(
                    returncode=0,
                    stdout=
                    'sky  7680  0.0  0.0 ... /python -m sky.skylet.skylet\nsky  7681  0.0  0.0 ... /python -m sky.skylet.skylet\n'
                )
            else:
                mock_result = mock.Mock(returncode=0)
            return mock_result

        monkeypatch.setattr('subprocess.run', mock_run)

        self.restart_skylet()

        # 2 calls: grep detection + nohup start
        assert len(subprocess_calls) == 2

        # Used grep-based detection fallback
        grep_cmd = subprocess_calls[0]
        assert 'grep "sky.skylet.skylet"' in grep_cmd

        # Started new skylet with hardcoded port
        nohup_cmd = subprocess_calls[1]
        assert f'--port={constants.SKYLET_GRPC_PORT}' in nohup_cmd

        # Killed old processes found via grep
        assert (7680, signal.SIGKILL) in killed_pids
        assert (7681, signal.SIGKILL) in killed_pids

        # Wrote files
        assert self.env['port_file'].read_text() == str(
            constants.SKYLET_GRPC_PORT)
        assert self.env['version_file'].read_text() == constants.SKYLET_VERSION
