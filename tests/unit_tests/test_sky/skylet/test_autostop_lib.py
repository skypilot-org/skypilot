"""Unit tests for skylet autostop_lib."""
from unittest import mock

import psutil
import pytest

from sky.skylet import autostop_lib


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
