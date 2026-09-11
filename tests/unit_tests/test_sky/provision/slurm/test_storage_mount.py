"""Unit tests for the Slurm storage-mount keeper handshake.

Exercises sky.provision.slurm.storage_mount against a real local filesystem
standing in for the shared cluster home: the stub head runner executes every
command with bash, so the spec write (heredoc + atomic rename), the marker
polls, and the log collection run the exact shell the runtime generates.
"""

import os
import shlex
import subprocess
import threading
import time
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import storage_mount


class _FakeHeadRunner:
    """Executes commands locally against a temp cluster home."""

    def __init__(self, sky_dir: str, node: str = 'node-0'):
        self.sky_dir = sky_dir
        self.slurm_node = node
        self.job_id = '123'
        self._on_spec_published = None

    def run_driver(self, cmd, **kwargs):
        del kwargs
        result = subprocess.run(['bash', '-c', cmd],
                                capture_output=True,
                                text=True,
                                check=False)
        if (self._on_spec_published is not None and f'mv -f ' in cmd and
                '.sh.tmp' in cmd):
            callback, self._on_spec_published = self._on_spec_published, None
            callback()
        return result.returncode, result.stdout, result.stderr

    def is_command_length_over_limit(self, cmd):
        del cmd
        return False

    def rsync_driver(self, *args, **kwargs):
        raise AssertionError('rsync should not be used for small specs')


def _make_cluster(tmp_path, nodes=('node-0', 'node-1'), keeper_ready=True):
    sky_dir = str(tmp_path / '.sky_clusters' / 'test-cluster')
    mount_dir = storage_mount.storage_mounts_dir(sky_dir)
    os.makedirs(mount_dir, exist_ok=True)
    if keeper_ready:
        with open(f'{mount_dir}/keeper_ready', 'w', encoding='utf-8'):
            pass
    head = _FakeHeadRunner(sky_dir)
    runners = [head]
    for node in nodes[1:]:
        runner = mock.MagicMock()
        runner.slurm_node = node
        runners.append(runner)
    return mount_dir, head, runners


def _patch_timing(monkeypatch):
    monkeypatch.setattr(storage_mount, 'POLL_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(storage_mount, 'KEEPER_PICKUP_TIMEOUT_SECONDS', 1)
    monkeypatch.setattr(storage_mount, 'STEP_EXIT_GRACE_SECONDS', 0.05)


_MOUNT_SPECS = [('/data', 'echo mount-cmd-body', 'Mounting', 's3://bkt')]


def test_build_spec_script_records_paths(tmp_path):
    mount_dir, _, _ = _make_cluster(tmp_path)
    spec = storage_mount._build_spec_script(_MOUNT_SPECS, mount_dir)
    assert spec.startswith('set -e\n')
    assert 'echo mount-cmd-body' in spec
    assert (f'p="$(eval echo /data)"; echo "${{p%/}}" >> '
            f'{shlex.quote(f"{mount_dir}/paths")}' in spec)
    assert shlex.quote('Mounting s3://bkt -> /data') in spec


def test_handshake_happy_path(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    # A ~-relative log path is what the backend passes (self.log_dir); the
    # local open() must expand it (remote shells would have).
    monkeypatch.setenv('HOME', str(tmp_path))
    mount_dir, head, runners = _make_cluster(tmp_path)
    log_path = '~/storage_mounts.log'

    def keeper_publishes():
        time.sleep(0.05)
        generation = _only_generation(mount_dir)
        with open(f'{mount_dir}/started-{generation}', 'w',
                  encoding='utf-8') as f:
            f.write(str(os.getpid()))
        for node in ('node-0', 'node-1'):
            os.makedirs(f'{mount_dir}/done-{generation}', exist_ok=True)
            with open(f'{mount_dir}/done-{generation}/{node}',
                      'w',
                      encoding='utf-8'):
                pass
            with open(
                    f'{mount_dir}/logs/storage_mounts-{node}-{generation}.log',
                    'w',
                    encoding='utf-8') as f:
                f.write(f'mounted on {node}\n')

    head._on_spec_published = keeper_publishes

    assert storage_mount.execute_storage_mounts(runners, _MOUNT_SPECS, log_path)
    # The spec was written with restrictive permissions.
    generation = _only_generation(mount_dir)
    spec_stat = os.stat(f'{mount_dir}/spec-{generation}.sh')
    assert spec_stat.st_mode & 0o777 == 0o600
    # Node logs were appended to the local storage_mounts.log.
    with open(os.path.expanduser(log_path), encoding='utf-8') as f:
        content = f.read()
    assert 'mounted on node-0' in content
    assert 'mounted on node-1' in content


def test_handshake_node_failure_raises_with_log_tail(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    mount_dir, head, runners = _make_cluster(tmp_path)
    log_path = str(tmp_path / 'storage_mounts.log')

    def keeper_publishes_failure():
        time.sleep(0.05)
        generation = _only_generation(mount_dir)
        with open(f'{mount_dir}/started-{generation}', 'w',
                  encoding='utf-8') as f:
            f.write(str(os.getpid()))
        os.makedirs(f'{mount_dir}/failed-{generation}', exist_ok=True)
        with open(f'{mount_dir}/failed-{generation}/node-1',
                  'w',
                  encoding='utf-8') as f:
            f.write('7')
        os.makedirs(f'{mount_dir}/logs', exist_ok=True)
        with open(f'{mount_dir}/logs/storage_mounts-node-1-{generation}.log',
                  'w',
                  encoding='utf-8') as f:
            f.write('goofys: mount failure detail\n')

    head._on_spec_published = keeper_publishes_failure

    with pytest.raises(exceptions.CommandError) as excinfo:
        storage_mount.execute_storage_mounts(runners, _MOUNT_SPECS, log_path)
    assert 'node-1' in str(excinfo.value)
    assert 'mount failure detail' in excinfo.value.detailed_reason
    # The failing node's log tail was copied into storage_mounts.log.
    with open(log_path, encoding='utf-8') as f:
        assert 'mount failure detail' in f.read()


def test_handshake_non_empty_mount_path_message(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    mount_dir, head, runners = _make_cluster(tmp_path, nodes=('node-0',))

    def keeper_publishes_failure():
        time.sleep(0.05)
        generation = _only_generation(mount_dir)
        with open(f'{mount_dir}/started-{generation}', 'w',
                  encoding='utf-8') as f:
            f.write(str(os.getpid()))
        os.makedirs(f'{mount_dir}/failed-{generation}', exist_ok=True)
        with open(f'{mount_dir}/failed-{generation}/node-0',
                  'w',
                  encoding='utf-8') as f:
            f.write(str(exceptions.MOUNT_PATH_NON_EMPTY_CODE))

    head._on_spec_published = keeper_publishes_failure

    with pytest.raises(RuntimeError, match='non-empty'):
        storage_mount.execute_storage_mounts(
            runners, _MOUNT_SPECS, str(tmp_path / 'storage_mounts.log'))


def test_handshake_step_died_before_markers(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    mount_dir, head, runners = _make_cluster(tmp_path)
    dead_process = subprocess.Popen(['true'])
    dead_process.wait()

    def keeper_publishes_dead_pid():
        time.sleep(0.05)
        generation = _only_generation(mount_dir)
        with open(f'{mount_dir}/started-{generation}', 'w',
                  encoding='utf-8') as f:
            f.write(str(dead_process.pid))

    head._on_spec_published = keeper_publishes_dead_pid

    with pytest.raises(exceptions.CommandError,
                       match='exited early') as excinfo:
        storage_mount.execute_storage_mounts(
            runners, _MOUNT_SPECS, str(tmp_path / 'storage_mounts.log'))
    # No node reported a result before the step died.
    assert 'unknown' in str(excinfo.value)


def test_handshake_falls_back_without_keeper(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    mount_dir, head, runners = _make_cluster(tmp_path, keeper_ready=False)
    with mock.patch.object(storage_mount.logger, 'warning') as warn:
        result = storage_mount.execute_storage_mounts(
            runners, _MOUNT_SPECS, str(tmp_path / 'storage_mounts.log'))
    assert result is False
    warn.assert_called_once()
    assert 'proctrack/cgroup' in str(warn.call_args)


def test_handshake_keeper_never_picks_up_spec(tmp_path, monkeypatch):
    _patch_timing(monkeypatch)
    monkeypatch.setattr(storage_mount, 'KEEPER_PICKUP_TIMEOUT_SECONDS', 0.1)
    mount_dir, head, runners = _make_cluster(tmp_path)
    # No on_spec_published hook: the started marker never appears.
    with pytest.raises(RuntimeError, match='did not pick up') as excinfo:
        storage_mount.execute_storage_mounts(
            runners, _MOUNT_SPECS, str(tmp_path / 'storage_mounts.log'))
    # The error points at the sbatch log.
    assert '.sky_provision/slurm-123.out' in str(excinfo.value)


def _only_generation(mount_dir: str) -> str:
    specs = [f for f in os.listdir(mount_dir) if f.startswith('spec-')]
    assert len(specs) == 1, specs
    return specs[0][len('spec-'):-len('.sh')]
