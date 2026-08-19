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


class TestRestartSkyletOnSlurm:
    """Test the Slurm (keeper-based) branch of restart_skylet()."""

    KEEPER_PID = 4242

    @pytest.fixture(autouse=True)
    def setup(self, skylet_env, tmp_path, monkeypatch):
        self.env = skylet_env
        self.start_file = tmp_path / 'skylet.start'
        monkeypatch.setattr(attempt_skylet, 'START_FILE', str(self.start_file))
        monkeypatch.setattr(attempt_skylet, '_is_inside_slurm_cluster',
                            lambda: True)
        monkeypatch.setattr('sky.utils.common_utils.find_free_port',
                            lambda port: constants.SKYLET_GRPC_PORT)

        # The nohup path must never run on Slurm.
        def forbid_run(cmd, **kwargs):
            raise AssertionError(f'unexpected subprocess.run on Slurm: {cmd}')

        monkeypatch.setattr('subprocess.run', forbid_run)
        # No real 30s waits: virtual clock, instant sleeps.
        self.clock = [0.0]
        fake_time = mock.Mock()
        fake_time.time = lambda: self.clock[0]

        def fake_sleep(seconds):
            self.clock[0] += seconds

        fake_time.sleep = fake_sleep
        monkeypatch.setattr(attempt_skylet, 'time', fake_time)

    def _simulate_keeper(self, monkeypatch):
        """Write the PID file once the start spec has landed, like the
        keeper's `bash spec` would, and validate only that exact PID."""

        # Kill scan finds nothing; the handshake must not use this either
        # way (see test_foreign_skylet_does_not_satisfy_handshake).
        monkeypatch.setattr(attempt_skylet, '_find_running_skylet_pids',
                            lambda: [])

        def fake_is_running(pid):
            return pid == self.KEEPER_PID

        monkeypatch.setattr(attempt_skylet, '_is_running_skylet_process',
                            fake_is_running)

        def fake_sleep_and_spawn(seconds):
            self.clock[0] += seconds
            if self.start_file.exists() and not self.env['pid_file'].exists():
                self.env['pid_file'].write_text(str(self.KEEPER_PID))

        # The keeper "runs" during the handshake's sleeps.
        monkeypatch.setattr(attempt_skylet.time, 'sleep', fake_sleep_and_spawn)

    def test_writes_spec_and_handshakes(self, monkeypatch):
        """Slurm branch writes the start spec and awaits the PID handshake."""
        self._simulate_keeper(monkeypatch)

        attempt_skylet.restart_skylet()

        spec = self.start_file.read_text()
        # PID-file semantics: the spec shell records $$, then exec replaces
        # the shell with skylet, so the file holds the real skylet PID.
        assert f'echo $$ > {self.env["pid_file"]}' in spec
        assert (f'exec {constants.SKY_PYTHON_CMD} -m sky.skylet.skylet '
                f'--port={constants.SKYLET_GRPC_PORT} '
                f'>> {self.env["log_file"]} 2>&1') in spec
        # Atomic write: no temp file left behind.
        assert not self.start_file.with_suffix(self.start_file.suffix +
                                               '.tmp').exists()
        assert not (self.start_file.parent /
                    (self.start_file.name + '.tmp')).exists()
        # Port/version files written in the usual order.
        assert self.env['port_file'].read_text() == str(
            constants.SKYLET_GRPC_PORT)
        assert self.env['version_file'].read_text() == constants.SKYLET_VERSION

    def test_removes_stale_spec_before_killing(self, monkeypatch):
        """A stale spec is removed before the kill, closing the relaunch race."""
        self.start_file.write_text('stale spec')
        stale_seen_at_kill_scan = []

        def fake_find_pids():
            # Only the kill scan uses this; the handshake must not.
            stale_seen_at_kill_scan.append(self.start_file.exists())
            return []

        self._simulate_keeper(monkeypatch)
        monkeypatch.setattr(attempt_skylet, '_find_running_skylet_pids',
                            fake_find_pids)

        attempt_skylet.restart_skylet()

        # The stale spec was already gone when the kill scan ran.
        assert stale_seen_at_kill_scan == [False]
        # And the fresh spec replaced it.
        assert 'stale spec' not in self.start_file.read_text()
        assert 'exec' in self.start_file.read_text()

    def test_handshake_timeout_raises_clear_error(self, monkeypatch):
        """No keeper response -> RuntimeError naming the keeper and files."""
        monkeypatch.setattr(attempt_skylet, '_find_running_skylet_pids',
                            lambda: [])

        with pytest.raises(RuntimeError, match='keeper'):
            attempt_skylet.restart_skylet()

        # The spec was still written; only the handshake failed.
        assert self.start_file.exists()

    def test_foreign_skylet_does_not_satisfy_handshake(self, monkeypatch):
        """Another cluster's skylet in the global ps scan is not a handshake."""
        # Global scan "sees" a foreign skylet the whole time; our PID file
        # never appears, and the foreign PID would pass a liveness check.
        monkeypatch.setattr(attempt_skylet, '_find_running_skylet_pids',
                            lambda: [9999])
        monkeypatch.setattr(attempt_skylet, '_is_running_skylet_process',
                            lambda pid: True)
        monkeypatch.setattr('os.kill', lambda p, s: None)
        monkeypatch.setattr('psutil.Process',
                            lambda p: mock.Mock(wait=lambda timeout: None))

        with pytest.raises(RuntimeError, match='keeper'):
            attempt_skylet.restart_skylet()

    def test_spec_is_written_with_owner_only_permissions(self, monkeypatch):
        """The spec can carry secrets in shared /tmp: mode must be 0600."""
        self._simulate_keeper(monkeypatch)

        attempt_skylet.restart_skylet()

        mode = self.start_file.stat().st_mode & 0o777
        assert mode == 0o600, oct(mode)

    def test_spec_serializes_launch_environment(self, monkeypatch):
        """The spec exports PATH (merged) and SKYPILOT_*/SKY_* vars, quoted."""
        import shlex

        self._simulate_keeper(monkeypatch)
        path_value = '/opt/cloud cli/bin:/usr/bin'
        monkeypatch.setenv('PATH', path_value)
        monkeypatch.setenv('HOME', '/root')
        monkeypatch.setenv('SKYPILOT_USER_ID', 'user-1')
        monkeypatch.setenv('SKY_RUNTIME_DIR', '/tmp/rt dir')
        monkeypatch.setenv('UNRELATED_SECRET', 'must-not-leak')

        attempt_skylet.restart_skylet()

        spec = self.start_file.read_text()
        exec_idx = spec.index('\nexec ')
        # PATH is prepended to the keeper's own PATH, not replaced: the spec
        # may be written in-container but runs on the host.
        path_line = f'export PATH={shlex.quote(path_value)}:"$PATH"'
        assert path_line in spec, path_line
        assert spec.index(path_line) < exec_idx
        # Every other export lands before the exec line, values shell-quoted.
        for key, value in (('SKYPILOT_USER_ID', 'user-1'), ('SKY_RUNTIME_DIR',
                                                            '/tmp/rt dir')):
            export_line = f'export {key}={shlex.quote(value)}'
            assert export_line in spec, export_line
            assert spec.index(export_line) < exec_idx
        # HOME is shape-dependent (in-container it is the isolated /root);
        # the keeper sets the host-correct HOME, so the spec must not.
        assert 'export HOME=' not in spec
        assert 'UNRELATED_SECRET' not in spec


class TestSlurmDetection:
    """Test _is_inside_slurm_cluster() marker resolution in both shapes."""

    def test_detects_slurm_via_runtime_dir_marker(self, tmp_path, monkeypatch):
        """Container shape: marker resolves through SKY_RUNTIME_DIR."""
        runtime_dir = tmp_path / 'rt'
        runtime_dir.mkdir()
        monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                           str(runtime_dir))
        monkeypatch.setenv('HOME', str(tmp_path / 'empty-home'))

        assert not attempt_skylet._is_inside_slurm_cluster()
        (runtime_dir / attempt_skylet._SLURM_MARKER_FILE).touch()
        assert attempt_skylet._is_inside_slurm_cluster()

    def test_detects_slurm_via_home_marker_fallback(self, tmp_path,
                                                    monkeypatch):
        """Host shape / pre-existing clusters: HOME marker still works."""
        home = tmp_path / 'cluster-home'
        home.mkdir()
        monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                           str(tmp_path / 'rt-no-marker'))
        monkeypatch.setenv('HOME', str(home))

        assert not attempt_skylet._is_inside_slurm_cluster()
        (home / attempt_skylet._SLURM_MARKER_FILE).touch()
        assert attempt_skylet._is_inside_slurm_cluster()
