"""Unit tests for cluster_event accessors in global_user_state."""
import time

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file (mirrors the helper in
    test_global_user_state_check_results.py)."""
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


def _add_cluster(name: str) -> str:
    """Create a minimal cluster row so add_cluster_event can find a hash.

    Returns the cluster_hash.
    """
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=None,
        requested_resources=set(),
        ready=False,
    )
    return global_user_state._get_hash_for_existing_cluster(name)


def test_launch_progress_excluded_from_last_event_helper(
        tmp_path, monkeypatch):
    """Adding a LAUNCH_PROGRESS event must not change the value returned by
    _get_last_or_terminal_cluster_event_multiple, which feeds the existing
    last_event field."""
    _fresh_db(tmp_path, monkeypatch)
    cluster_hash = _add_cluster('c1')

    # 1. Write a STATUS_CHANGE event first (older).
    global_user_state.add_cluster_event(
        'c1',
        new_status=None,
        reason='status-change-reason',
        event_type=global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=1,
    )
    # 2. Then a newer LAUNCH_PROGRESS event.
    global_user_state.add_cluster_event(
        'c1',
        new_status=None,
        reason='launch-progress-reason',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=2,
    )

    result = global_user_state._get_last_or_terminal_cluster_event_multiple(
        {cluster_hash})
    # The helper must skip the LAUNCH_PROGRESS row and return the
    # STATUS_CHANGE reason.
    assert result == {cluster_hash: 'status-change-reason'}


def test_launch_progress_retention_cleans_old_rows(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c2')

    # Insert one ancient event and one recent one.
    now = int(time.time())
    global_user_state.add_cluster_event(
        'c2',
        new_status=None,
        reason='old-launch-step',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=now - 24 * 3600,  # 24h ago
    )
    global_user_state.add_cluster_event(
        'c2',
        new_status=None,
        reason='recent-launch-step',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=now,
    )

    # Retention = 1h → old row should be deleted, recent row should remain.
    global_user_state.cleanup_cluster_events_with_retention(
        retention_hours=1,
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    )

    cluster_hash = global_user_state._get_hash_for_existing_cluster('c2')
    remaining = global_user_state.get_last_cluster_event(
        cluster_hash,
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    )
    assert remaining == 'recent-launch-step'
