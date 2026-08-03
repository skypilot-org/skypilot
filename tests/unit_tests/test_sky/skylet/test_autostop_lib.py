"""Unit tests for skylet autostop_lib."""
import pickle
import threading
from unittest import mock

import psutil
import pytest

from sky.skylet import autostop_lib
from sky.skylet import configs
from sky.skylet import runtime_utils


@pytest.fixture
def isolated_autostop_storage(tmp_path, monkeypatch):
    database_dir = tmp_path / 'skylet-config'
    database_dir.mkdir()
    monkeypatch.setattr(configs, '_DB_PATH', None)
    monkeypatch.setattr(
        runtime_utils, 'get_runtime_dir_path',
        lambda relative_path: str(database_dir / relative_path.lstrip('/')))
    monkeypatch.setattr(autostop_lib,
                        '_AUTOSTOP_CONFIG_LOCK_PATH',
                        str(tmp_path / 'autostop-config.lock'),
                        raising=False)


def _set_durable_autodown(*, cluster_hash='cluster-hash', generation=7):
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash=cluster_hash,
        generation=generation,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_old_autostop_pickle_defaults_to_legacy_execution():
    legacy_config = autostop_lib.AutostopConfig(
        autostop_idle_minutes=10,
        boot_time=123,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
    )
    for field in ('cluster_hash', 'generation', 'execution_strategy',
                  'durable_execution_state', 'error_summary'):
        legacy_config.__dict__.pop(field, None)

    restored = pickle.loads(pickle.dumps(legacy_config))

    assert restored.cluster_hash is None
    assert restored.generation is None
    assert (restored.execution_strategy ==
            autostop_lib.AutodownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)
    assert (restored.durable_execution_state ==
            autostop_lib.DurableAutodownState.ARMED)
    assert restored.error_summary is None


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_durable_state_transitions_are_generation_and_hash_fenced():
    _set_durable_autodown()

    assert not autostop_lib.mark_head_teardown_started('stale-hash', 7)
    assert not autostop_lib.mark_head_teardown_started('cluster-hash', 6)
    armed = autostop_lib.get_autostop_config()
    assert (armed.durable_execution_state ==
            autostop_lib.DurableAutodownState.ARMED)

    assert autostop_lib.mark_head_teardown_started('cluster-hash', 7)
    started = autostop_lib.get_autostop_config()
    assert (started.durable_execution_state ==
            autostop_lib.DurableAutodownState.HEAD_TEARDOWN_STARTED)

    assert autostop_lib.mark_server_teardown_required('cluster-hash', 7,
                                                      'bounded failure')
    fallback = autostop_lib.get_autostop_config()
    assert (fallback.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert fallback.error_summary == 'bounded failure'


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_new_setting_and_cancellation_reset_armed_durable_intent():
    _set_durable_autodown()

    _set_durable_autodown(cluster_hash='replacement-hash', generation=8)
    replacement = autostop_lib.get_autostop_config()
    assert (replacement.durable_execution_state ==
            autostop_lib.DurableAutodownState.ARMED)
    assert replacement.error_summary is None

    autostop_lib.set_autostop(
        idle_minutes=-1,
        backend=None,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='replacement-hash',
        generation=9,
        execution_strategy=(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY),
    )
    cancelled = autostop_lib.get_autostop_config()
    assert (cancelled.durable_execution_state ==
            autostop_lib.DurableAutodownState.UNSPECIFIED)
    assert cancelled.error_summary is None


@pytest.mark.usefixtures('isolated_autostop_storage')
@pytest.mark.parametrize(
    'claimed_state',
    [
        autostop_lib.DurableAutodownState.HEAD_TEARDOWN_STARTED,
        autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED,
    ],
)
def test_new_generation_cannot_cancel_irreversibly_claimed_teardown(
        claimed_state):
    _set_durable_autodown()
    assert autostop_lib.mark_head_teardown_started('cluster-hash', 7)
    if claimed_state == autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED:
        assert autostop_lib.mark_server_teardown_required(
            'cluster-hash', 7, 'head preparation failed')

    result = autostop_lib.set_autostop(
        idle_minutes=-1,
        backend=None,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='cluster-hash',
        generation=8,
        execution_strategy=autostop_lib.AutodownExecutionStrategy.SERVER_ONLY,
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    stored = autostop_lib.get_autostop_config()
    assert stored.generation == 7
    assert stored.durable_execution_state == claimed_state


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_durable_error_summary_is_bounded():
    _set_durable_autodown()

    assert autostop_lib.mark_server_teardown_required(
        'cluster-hash', 7,
        'x' * (autostop_lib.MAX_DURABLE_ERROR_SUMMARY_LENGTH + 100))

    summary = autostop_lib.get_autostop_config().error_summary
    assert summary is not None
    assert len(summary) == autostop_lib.MAX_DURABLE_ERROR_SUMMARY_LENGTH


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_strict_autostop_rejects_same_generation_conflicting_config():
    _set_durable_autodown()

    result = autostop_lib.set_autostop(
        idle_minutes=11,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='cluster-hash',
        generation=7,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    stored = autostop_lib.get_autostop_config()
    assert stored.autostop_idle_minutes == 10
    assert stored.cluster_hash == 'cluster-hash'
    assert stored.generation == 7


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_strict_autostop_rejects_same_generation_different_hash():
    _set_durable_autodown()

    result = autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='conflicting-hash',
        generation=7,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    assert autostop_lib.get_autostop_config().cluster_hash == 'cluster-hash'


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_strict_autostop_rejects_same_generation_conflicting_hooks():
    hooks = [{
        'run': 'echo first',
        'events': ['down'],
        'timeout': 60,
    }]
    _set_durable_autodown()
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hooks=hooks,
        cluster_hash='hooks-hash',
        generation=8,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    result = autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hooks=[{
            'run': 'echo conflicting',
            'events': ['down'],
            'timeout': 60,
        }],
        cluster_hash='hooks-hash',
        generation=8,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    assert autostop_lib.get_hooks() == hooks


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_exact_strict_autostop_replay_keeps_timer_state_and_legacy_hook():
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hook='echo durable hook',
        cluster_hash='cluster-hash',
        generation=7,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )
    assert autostop_lib.mark_head_teardown_started('cluster-hash', 7)
    original_hooks = autostop_lib.get_hooks()

    with mock.patch.object(autostop_lib,
                           'set_last_active_time_to_now') as reset_timer, \
            mock.patch.object(autostop_lib, 'set_hooks') as set_hooks:
        result = autostop_lib.set_autostop(
            idle_minutes=10,
            backend='cloud-vm-ray',
            wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
            down=True,
            hook='echo durable hook',
            cluster_hash='cluster-hash',
            generation=7,
            execution_strategy=(autostop_lib.AutodownExecutionStrategy.
                                HEAD_WITH_SERVER_FALLBACK),
        )

    assert result == autostop_lib.AutostopConfigUpdateResult.REPLAYED
    reset_timer.assert_not_called()
    set_hooks.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.HEAD_TEARDOWN_STARTED)
    assert autostop_lib.get_hooks() == original_hooks


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_parent_persisted_strict_config_replays_with_stored_hooks():
    hooks = [{
        'run': 'echo durable hook',
        'events': ['down'],
        'timeout': 60,
    }]
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hooks=hooks,
        cluster_hash='cluster-hash',
        generation=7,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )
    parent_config = autostop_lib.get_autostop_config()
    parent_config.durable_hooks = None
    configs.set_config(autostop_lib._AUTOSTOP_CONFIG_KEY,
                       pickle.dumps(parent_config))

    result = autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hooks=hooks,
        cluster_hash='cluster-hash',
        generation=7,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REPLAYED
    assert autostop_lib.get_hooks() == hooks


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_legacy_autostop_cannot_overwrite_strict_config():
    _set_durable_autodown()

    result = autostop_lib.set_autostop(
        idle_minutes=-1,
        backend=None,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    stored = autostop_lib.get_autostop_config()
    assert stored.cluster_hash == 'cluster-hash'
    assert stored.generation == 7


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_new_strict_hash_generation_rejects_delayed_old_hash():
    _set_durable_autodown(cluster_hash='first-hash', generation=1)
    assert autostop_lib.set_autostop(
        idle_minutes=20,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='second-hash',
        generation=2,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    ) == autostop_lib.AutostopConfigUpdateResult.APPLIED

    result = autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='first-hash',
        generation=1,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )

    assert result == autostop_lib.AutostopConfigUpdateResult.REJECTED
    stored = autostop_lib.get_autostop_config()
    assert stored.cluster_hash == 'second-hash'
    assert stored.generation == 2
    assert stored.autostop_idle_minutes == 20


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_concurrent_delayed_strict_generation_cannot_overwrite_newer_config(
        monkeypatch):
    original_get_lock = autostop_lib._get_autostop_config_lock
    delayed_writer_at_barrier = threading.Barrier(2)
    resume_delayed_writer = threading.Event()

    class _DelayedLock:
        """Pauses generation one before it acquires the real file lock."""

        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            delayed_writer_at_barrier.wait(timeout=2)
            assert resume_delayed_writer.wait(timeout=2)
            return self._lock.__enter__()

        def __exit__(self, *args):
            return self._lock.__exit__(*args)

    def get_lock():
        lock = original_get_lock()
        if threading.current_thread().name == 'delayed-generation-1':
            return _DelayedLock(lock)
        return lock

    monkeypatch.setattr(autostop_lib, '_get_autostop_config_lock', get_lock)
    results = {}

    def write_generation_one():
        results['generation_one'] = autostop_lib.set_autostop(
            idle_minutes=10,
            backend='cloud-vm-ray',
            wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
            down=True,
            cluster_hash='first-hash',
            generation=1,
            execution_strategy=(autostop_lib.AutodownExecutionStrategy.
                                HEAD_WITH_SERVER_FALLBACK),
        )

    delayed_writer = threading.Thread(target=write_generation_one,
                                      name='delayed-generation-1')
    delayed_writer.start()
    delayed_writer_at_barrier.wait(timeout=2)
    try:
        results['generation_two'] = autostop_lib.set_autostop(
            idle_minutes=20,
            backend='cloud-vm-ray',
            wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
            down=True,
            cluster_hash='second-hash',
            generation=2,
            execution_strategy=(autostop_lib.AutodownExecutionStrategy.
                                HEAD_WITH_SERVER_FALLBACK),
        )
    finally:
        resume_delayed_writer.set()
    delayed_writer.join(timeout=2)

    assert not delayed_writer.is_alive()
    assert results['generation_one'] == (
        autostop_lib.AutostopConfigUpdateResult.REJECTED)
    assert results['generation_two'] == (
        autostop_lib.AutostopConfigUpdateResult.APPLIED)
    stored = autostop_lib.get_autostop_config()
    assert stored.cluster_hash == 'second-hash'
    assert stored.generation == 2
    assert stored.autostop_idle_minutes == 20


def _fake_proc(pid, terminal=None):
    proc = mock.MagicMock()
    proc.info = {'pid': pid, 'terminal': terminal}
    proc.pid = pid
    return proc


def _patch_process_iter(monkeypatch, procs):
    monkeypatch.setattr(psutil,
                        'process_iter',
                        lambda fields=None: iter(list(procs)))


class TestHasActiveSshSessions:
    """Tests for has_active_ssh_sessions().

    Regression coverage for
    https://github.com/skypilot-org/skypilot/issues/9524 — psutil memoizes
    /dev/{tty*,pts/*} -> rdev at first call and never invalidates, so a
    skylet daemon that booted with no SSH session sees a frozen empty
    /dev/pts map for its lifetime.
    """

    def test_clears_psutil_terminal_map_cache_each_call(self, monkeypatch):
        """The fix must invalidate psutil's get_terminal_map cache."""
        cache_clear = mock.MagicMock()
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            cache_clear)
        _patch_process_iter(monkeypatch, [])

        autostop_lib.has_active_ssh_sessions()
        autostop_lib.has_active_ssh_sessions()

        assert cache_clear.call_count == 2

    def test_returns_true_when_pty_ancestor_is_sshd(self, monkeypatch):
        sshd = mock.MagicMock()
        sshd.name.return_value = 'sshd'
        not_sshd = mock.MagicMock()
        not_sshd.name.return_value = 'systemd'

        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())
        _patch_process_iter(monkeypatch, [
            _fake_proc(pid=1234, terminal='/dev/pts/0'),
            _fake_proc(pid=999, terminal=None),
        ])

        proc_for_pid = mock.MagicMock()
        proc_for_pid.parents.return_value = [not_sshd, sshd]
        monkeypatch.setattr(psutil, 'Process', lambda pid: proc_for_pid)

        assert autostop_lib.has_active_ssh_sessions() is True

    def test_returns_false_when_no_pty_processes(self, monkeypatch):
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())
        _patch_process_iter(monkeypatch, [
            _fake_proc(pid=1, terminal=None),
            _fake_proc(pid=2, terminal=None)
        ])

        assert autostop_lib.has_active_ssh_sessions() is False

    def test_returns_false_when_pty_not_under_sshd(self, monkeypatch):
        """A local tmux/screen PTY without sshd ancestor is not an SSH."""
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())
        _patch_process_iter(monkeypatch,
                            [_fake_proc(pid=1234, terminal='/dev/pts/0')])

        tmux = mock.MagicMock()
        tmux.name.return_value = 'tmux'
        proc = mock.MagicMock()
        proc.parents.return_value = [tmux]
        monkeypatch.setattr(psutil, 'Process', lambda pid: proc)

        assert autostop_lib.has_active_ssh_sessions() is False

    def test_does_not_raise_if_psutil_private_api_changes(self, monkeypatch):
        """If psutil rearranges _psposix, the fix must degrade gracefully."""

        class _GetTerminalMapWithoutCacheClear:

            def __call__(self):
                return {}

        monkeypatch.setattr(psutil._psposix, 'get_terminal_map',
                            _GetTerminalMapWithoutCacheClear())
        _patch_process_iter(monkeypatch, [])

        # No exception even though cache_clear is missing.
        assert autostop_lib.has_active_ssh_sessions() is False

    def test_skips_pid_that_dies_between_iter_and_parents(self, monkeypatch):
        """A PID disappearing during the parents() walk must not abort.

        Covers the `except psutil.NoSuchProcess` branch: the inner loop
        must `continue` so a subsequent live SSH PID is still detected.
        """
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())
        # Two distinct PTYs -> two PIDs to walk.
        _patch_process_iter(monkeypatch, [
            _fake_proc(pid=1234, terminal='/dev/pts/0'),
            _fake_proc(pid=5678, terminal='/dev/pts/1'),
        ])

        sshd = mock.MagicMock()
        sshd.name.return_value = 'sshd'
        live_proc = mock.MagicMock()
        live_proc.parents.return_value = [sshd]
        dead_proc = mock.MagicMock()
        dead_proc.parents.side_effect = psutil.NoSuchProcess(pid=1234)

        # dict iteration over pts_to_pid is order-preserving (Python 3.7+),
        # so 1234 is queried first, raises, we continue to 5678 and find
        # sshd in its ancestry.
        process_calls = {1234: dead_proc, 5678: live_proc}
        monkeypatch.setattr(psutil, 'Process', lambda pid: process_calls[pid])

        assert autostop_lib.has_active_ssh_sessions() is True
        dead_proc.parents.assert_called_once()
        live_proc.parents.assert_called_once()

    def test_skips_pid_with_access_denied(self, monkeypatch):
        """psutil.AccessDenied on parents() must also be swallowed."""
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())
        _patch_process_iter(monkeypatch,
                            [_fake_proc(pid=1234, terminal='/dev/pts/0')])
        denied_proc = mock.MagicMock()
        denied_proc.parents.side_effect = psutil.AccessDenied(pid=1234)
        monkeypatch.setattr(psutil, 'Process', lambda pid: denied_proc)

        # Only one PID, parents() denies; nothing else found -> False, no
        # exception escapes.
        assert autostop_lib.has_active_ssh_sessions() is False

    def test_returns_false_on_unexpected_exception(self, monkeypatch):
        """Outer broad-except: any unhandled error -> False + warning log.

        Covers the safety-net branch so an unexpected psutil failure can
        never crash the skylet daemon.
        """
        monkeypatch.setattr(psutil._psposix.get_terminal_map, 'cache_clear',
                            mock.MagicMock())

        def _boom(*_args, **_kwargs):
            raise RuntimeError('simulated psutil failure')

        monkeypatch.setattr(psutil, 'process_iter', _boom)
        # The `sky` logger has propagate=False, so use a direct mock on
        # the module logger instead of caplog to observe the warning.
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(autostop_lib.logger, 'warning', warning_mock)

        result = autostop_lib.has_active_ssh_sessions()

        assert result is False
        warning_mock.assert_called_once()
        msg = warning_mock.call_args.args[0]
        assert 'Error checking active SSH sessions' in msg
        assert 'simulated psutil failure' in msg

    def test_invalidates_primed_empty_cache(self, monkeypatch):
        """The fix must actually drop a primed empty terminal map.

        Reproduces the post-`sky stop`/`sky start` state by forcing
        psutil's @memoize cache to materialize as an empty dict, then
        verifies has_active_ssh_sessions() leaves the cache empty (i.e.
        cleared) so that a fresh call to get_terminal_map() would
        re-glob /dev/pts/.
        """
        psutil._psposix.get_terminal_map.cache_clear()
        with mock.patch('glob.glob', return_value=[]):
            primed = psutil._psposix.get_terminal_map()
        assert primed == {}, 'failed to prime the bug state'
        # Confirm @memoize stored the empty result.
        with mock.patch('glob.glob',
                        side_effect=AssertionError('cache not used')):
            assert psutil._psposix.get_terminal_map() == {}

        _patch_process_iter(monkeypatch, [])
        autostop_lib.has_active_ssh_sessions()

        # After the fix runs, calling get_terminal_map() again must
        # re-glob /dev/* — i.e. the cache was invalidated.
        re_globbed = mock.MagicMock(return_value=[])
        with mock.patch('glob.glob', re_globbed):
            psutil._psposix.get_terminal_map()
        assert re_globbed.called, (
            'cache was not invalidated; glob.glob was not re-called')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
