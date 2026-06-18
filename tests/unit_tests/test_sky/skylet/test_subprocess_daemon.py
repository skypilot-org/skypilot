"""Unit tests for sky.skylet.subprocess_daemon orphan reaping logic."""
from unittest import mock

import psutil

from sky.skylet import subprocess_daemon as sd

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


# ---------------- _process_group_orphaned ----------------

# proc_pgid (the watched process's group) is 1000 in these tests.
_PROC_PGID = 1000


def test_pg_orphaned_same_group_as_watched_reaps():
    # In the watched process's own group (pgid == proc_pgid): a direct
    # workload process (job-control-off / VM case) -> reap (True). The group
    # leader is the still-alive watched process, so the os.kill probe would
    # WRONGLY spare it; the same-group check must short-circuit to reap.
    with mock.patch.object(sd.os, 'getpgid', return_value=_PROC_PGID):
        with mock.patch.object(sd.os, 'kill', return_value=None):
            assert sd._process_group_orphaned(501, _PROC_PGID) is True


def test_pg_orphaned_dead_leader_reaps():
    # Separate group whose leader is gone -> orphaned group -> reap (True).
    with mock.patch.object(sd.os, 'getpgid', return_value=500):
        with mock.patch.object(sd.os, 'kill', side_effect=ProcessLookupError):
            assert sd._process_group_orphaned(501, _PROC_PGID) is True


def test_pg_orphaned_live_leader_spares():
    # Separate group, leader alive (os.kill(pgid, 0) succeeds) -> spare.
    # This is the ray-GCS-under-live-start_cluster case.
    with mock.patch.object(sd.os, 'getpgid', return_value=500):
        with mock.patch.object(sd.os, 'kill', return_value=None):
            assert sd._process_group_orphaned(501, _PROC_PGID) is False


def test_pg_orphaned_getpgid_error_spares():
    with mock.patch.object(sd.os, 'getpgid', side_effect=ProcessLookupError):
        assert sd._process_group_orphaned(501, _PROC_PGID) is False


def test_pg_orphaned_permission_error_spares():
    # Separate group, leader owned by another uid -> alive -> spare (False).
    with mock.patch.object(sd.os, 'getpgid', return_value=500):
        with mock.patch.object(sd.os, 'kill', side_effect=PermissionError):
            assert sd._process_group_orphaned(501, _PROC_PGID) is False


def test_pg_orphaned_none_proc_pgid_falls_back_to_leader_check():
    # If the watched group is unknown, fall back to leader-liveness only.
    with mock.patch.object(sd.os, 'getpgid', return_value=500):
        with mock.patch.object(sd.os, 'kill', side_effect=ProcessLookupError):
            assert sd._process_group_orphaned(501, None) is True


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
