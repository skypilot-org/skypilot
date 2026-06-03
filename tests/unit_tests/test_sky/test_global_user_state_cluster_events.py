"""Unit tests for cluster_event accessors in global_user_state."""
import time

from sky import global_user_state
from sky.skylet import constants
from sky.utils import status_lib
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


class _MinimalHandle:
    """Minimal handle that satisfies get_clusters' attribute access."""
    launched_resources = None


def _add_cluster(name: str) -> str:
    """Create a minimal cluster row so add_cluster_event can find a hash.

    Returns the cluster_hash.
    """
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=False,
    )
    return global_user_state._get_hash_for_existing_cluster(name)


def test_launch_progress_excluded_from_last_event_helper(tmp_path, monkeypatch):
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


def test_get_last_event_of_type_multiple(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    h1 = _add_cluster('a')
    h2 = _add_cluster('b')

    global_user_state.add_cluster_event(
        'a',
        new_status=None,
        reason='a-old',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=1,
    )
    global_user_state.add_cluster_event(
        'a',
        new_status=None,
        reason='a-new',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=2,
    )
    global_user_state.add_cluster_event(
        'b',
        new_status=None,
        reason='b-only',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=5,
    )
    # Wrong-type row for 'b' to verify the filter:
    global_user_state.add_cluster_event(
        'b',
        new_status=None,
        reason='b-status-change',
        event_type=global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=10,
    )

    result = global_user_state.get_last_cluster_event_of_type_multiple(
        {h1, h2},
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    )
    assert result == {h1: 'a-new', h2: 'b-only'}


def test_get_clusters_populates_launch_status_reason_for_init(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('init-cluster')
    # add_or_update_cluster default leaves the row in INIT.
    global_user_state.add_cluster_event(
        'init-cluster',
        new_status=None,
        reason='Launching (1 pod(s) pending due to Pulling)',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=int(time.time()),
    )

    # Both summary and full responses must carry the field.
    for summary in (True, False):
        records = global_user_state.get_clusters(summary_response=summary)
        match = [r for r in records if r['name'] == 'init-cluster']
        assert len(match) == 1
        assert match[0]['status'] is status_lib.ClusterStatus.INIT
        assert match[0]['launch_status_reason'] == (
            'Launching (1 pod(s) pending due to Pulling)')


def test_get_clusters_no_launch_status_reason_for_up(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('up-cluster')
    # Bring the cluster to UP.
    global_user_state.add_or_update_cluster(
        cluster_name='up-cluster',
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
    )
    # Even with a LAUNCH_PROGRESS event present, an UP cluster's field
    # must be None.
    global_user_state.add_cluster_event(
        'up-cluster',
        new_status=None,
        reason='stale-launch-step',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=int(time.time()),
    )
    records = global_user_state.get_clusters(summary_response=False)
    match = [r for r in records if r['name'] == 'up-cluster']
    assert len(match) == 1
    assert match[0]['status'] is status_lib.ClusterStatus.UP
    assert match[0].get('launch_status_reason') is None
