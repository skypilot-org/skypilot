"""Unit tests for sky.skylet.subprocess_daemon orphan/wedge reaping logic."""
import time
from unittest import mock

import psutil

from sky.skylet import subprocess_daemon as sd


class _FakeProc:
    """Minimal stand-in for psutil.Process for the sweep logic."""

    def __init__(self, pid, ppid, status=psutil.STATUS_SLEEPING, name='python'):
        self.pid = pid
        self._ppid = ppid
        self._status = status
        self._name = name

    def status(self):
        return self._status

    def ppid(self):
        return self._ppid

    def name(self):
        return self._name


# ---------------- _same_session ----------------


def test_same_session_matches():
    with mock.patch.object(sd.os, 'getsid', return_value=42):
        assert sd._same_session(123, 42) is True


def test_same_session_differs():
    with mock.patch.object(sd.os, 'getsid', return_value=7):
        assert sd._same_session(123, 42) is False


def test_same_session_none_ref_spares():
    # Unknown reference session -> err on the side of sparing (False).
    assert sd._same_session(123, None) is False


def test_same_session_lookup_error_spares():
    with mock.patch.object(sd.os, 'getsid', side_effect=ProcessLookupError):
        assert sd._same_session(123, 42) is False
    with mock.patch.object(sd.os, 'getsid', side_effect=OSError):
        assert sd._same_session(123, 42) is False


# ---------------- _zombie_wedge_sweep ----------------

_PROC_PID = 100
_PARENT_PID = 50
_PROC_SID = 100
_WEDGED_PID = 200
_ZOMBIE_PID = 999


def _run_sweep(monkeypatch, *, aged, wedged_parent, wedged_sid):
    """Drive one sweep tick; return the _term_then_kill mock."""
    zombie = _FakeProc(_ZOMBIE_PID, _WEDGED_PID, status=psutil.STATUS_ZOMBIE)
    wedged = _FakeProc(_WEDGED_PID, wedged_parent)

    monkeypatch.setattr(sd.psutil, 'Process', lambda pid: wedged)
    monkeypatch.setattr(sd.os, 'getsid', lambda pid: wedged_sid)
    killer = mock.Mock(return_value='kill')
    monkeypatch.setattr(sd, '_term_then_kill', killer)

    zombie_first_seen = {}
    if aged:
        # Pre-seed so the zombie is already older than the grace period.
        zombie_first_seen[_ZOMBIE_PID] = time.monotonic() - 10_000
    sd._zombie_wedge_sweep([zombie], zombie_first_seen, 60.0, _PROC_PID,
                           _PARENT_PID, _PROC_SID)
    return killer


def test_wedge_kills_direct_child_in_session(monkeypatch):
    # Wedged parent is a direct child of proc_pid and in-session -> killed.
    killer = _run_sweep(monkeypatch,
                        aged=True,
                        wedged_parent=_PROC_PID,
                        wedged_sid=_PROC_SID)
    killer.assert_called_once()


def test_wedge_spares_deep_descendant(monkeypatch):
    # Zombie's parent is NOT a direct child of proc_pid (it's infrastructure,
    # e.g. a controller executor) -> structural gate spares it.
    killer = _run_sweep(monkeypatch,
                        aged=True,
                        wedged_parent=12345,
                        wedged_sid=_PROC_SID)
    killer.assert_not_called()


def test_wedge_spares_detached_session(monkeypatch):
    # Direct child of proc_pid but setsid'd into its own session -> spared.
    killer = _run_sweep(monkeypatch,
                        aged=True,
                        wedged_parent=_PROC_PID,
                        wedged_sid=777)
    killer.assert_not_called()


def test_wedge_spares_young_zombie(monkeypatch):
    # Zombie younger than the grace period -> no action yet.
    killer = _run_sweep(monkeypatch,
                        aged=False,
                        wedged_parent=_PROC_PID,
                        wedged_sid=_PROC_SID)
    killer.assert_not_called()


def test_wedge_spares_protected_ancestors(monkeypatch):
    # If the zombie's parent is proc_pid/parent_pid/1/0, never kill.
    for protected in (0, 1, _PROC_PID, _PARENT_PID):
        zombie = _FakeProc(_ZOMBIE_PID, protected, status=psutil.STATUS_ZOMBIE)
        killer = mock.Mock(return_value='kill')
        monkeypatch.setattr(sd, '_term_then_kill', killer)
        sd._zombie_wedge_sweep([zombie], {_ZOMBIE_PID: time.monotonic() - 1e4},
                               60.0, _PROC_PID, _PARENT_PID, _PROC_SID)
        killer.assert_not_called()


# ---------------- daemon intermediate reaper ----------------


def test_reap_finished_daemon_procs_prunes_only_exited():
    finished = mock.Mock()
    finished.poll.return_value = 0  # exited (reaped)
    running = mock.Mock()
    running.poll.return_value = None  # still alive
    sd_utils = __import__('sky.utils.subprocess_utils',
                          fromlist=['subprocess_utils'])
    with mock.patch.object(sd_utils, '_pending_daemon_procs',
                           [finished, running]):
        sd_utils._reap_finished_daemon_procs()
        assert sd_utils._pending_daemon_procs == [running]
