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


def test_get_last_event_of_type_multiple_chunks_large_sets(
        tmp_path, monkeypatch):
    """More hashes than one IN clause may hold are queried in chunks and
    merged, so a large pool's status does not exceed SQLite's bind limit."""
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE', 2)
    expected = {}
    for i in range(5):
        name = f'c{i}'
        cluster_hash = _add_cluster(name)
        for t, reason in ((1, f'{name}-old'), (2, f'{name}-new')):
            global_user_state.add_cluster_event(
                name,
                new_status=None,
                reason=reason,
                event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
                transitioned_at=t,
            )
        expected[cluster_hash] = f'{name}-new'

    result = global_user_state.get_last_cluster_event_of_type_multiple(
        set(expected),
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    )
    assert result == expected


def test_get_clusters_marks_launching_init(tmp_path, monkeypatch):
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

    # Both summary and full responses must carry the classification.
    for summary in (True, False):
        records = global_user_state.get_clusters(summary_response=summary)
        match = [r for r in records if r['name'] == 'init-cluster']
        assert len(match) == 1
        assert match[0]['status'] is status_lib.ClusterStatus.INIT
        assert match[0]['init_kind'] == 'launching'
        assert match[0]['init_status_reason'] == (
            'Launching (1 pod(s) pending due to Pulling)')


def test_get_clusters_no_init_classification_for_up(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('up-cluster')
    # Bring the cluster to UP.
    global_user_state.add_or_update_cluster(
        cluster_name='up-cluster',
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
    )
    # Even with a LAUNCH_PROGRESS event present, an UP cluster's fields
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
    # An UP cluster has no INIT sub-classification.
    assert match[0].get('init_kind') is None
    assert match[0].get('init_status_reason') is None


def _abnormal_reason(detail: str) -> str:
    return f'{global_user_state.ABNORMAL_STATUS_REASON_PREFIX} {detail}'


def test_get_last_event_of_types_returns_newest_across_types(
        tmp_path, monkeypatch):
    """With a list of types, the newest event across them wins; other types
    (e.g. DEBUG) are ignored even when more recent."""
    _fresh_db(tmp_path, monkeypatch)
    h = _add_cluster('c')
    global_user_state.add_cluster_event(
        'c',
        new_status=None,
        reason='launch-step',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=1,
    )
    global_user_state.add_cluster_event(
        'c',
        new_status=None,
        reason=_abnormal_reason('one or more nodes terminated'),
        event_type=global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=2,
    )
    # A DEBUG row must be ignored even though it is the newest.
    global_user_state.add_cluster_event(
        'c',
        new_status=None,
        reason='debug-noise',
        event_type=global_user_state.ClusterEventType.DEBUG,
        transitioned_at=3,
    )
    result = global_user_state.get_last_cluster_event_of_type_multiple({h}, [
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    ])
    assert result == {h: _abnormal_reason('one or more nodes terminated')}


def test_get_clusters_marks_abnormal_init_as_unhealthy(tmp_path, monkeypatch):
    """A cluster whose newest event is an abnormal-state STATUS_CHANGE is
    classified 'unhealthy' with the abnormal reason surfaced."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('sick')
    reason = _abnormal_reason(
        '1 of 1 node(s) in unexpected state: Failed (OOMKilled)')
    global_user_state.add_cluster_event(
        'sick',
        new_status=status_lib.ClusterStatus.INIT,
        reason=reason,
        event_type=global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=int(time.time()),
    )
    for summary in (True, False):
        records = global_user_state.get_clusters(summary_response=summary)
        match = [r for r in records if r['name'] == 'sick']
        assert len(match) == 1
        assert match[0]['status'] is status_lib.ClusterStatus.INIT
        assert match[0]['init_kind'] == 'unhealthy'
        assert match[0]['init_status_reason'] == reason


def test_get_clusters_relaunch_supersedes_unhealthy(tmp_path, monkeypatch):
    """A newer launch event after an abnormal transition flips the cluster
    back to 'launching' (it is being actively relaunched)."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('relaunch')
    global_user_state.add_cluster_event(
        'relaunch',
        new_status=status_lib.ClusterStatus.INIT,
        reason=_abnormal_reason('ray cluster is unhealthy'),
        event_type=global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=10,
    )
    # A newer launch-progress event (the user re-ran `sky launch`).
    global_user_state.add_cluster_event(
        'relaunch',
        new_status=None,
        reason='Launching (Kubernetes cluster is autoscaling)',
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
        transitioned_at=20,
    )
    records = global_user_state.get_clusters(summary_response=False)
    match = [r for r in records if r['name'] == 'relaunch']
    assert len(match) == 1
    assert match[0]['init_kind'] == 'launching'
    assert match[0]['init_status_reason'] == (
        'Launching (Kubernetes cluster is autoscaling)')


def test_get_cluster_events_multiple_types_merged_and_ordered(
        tmp_path, monkeypatch):
    """get_cluster_events accepts a list of types and merges them in one query,
    ordered oldest-to-newest, with limit applied across the combined set."""
    _fresh_db(tmp_path, monkeypatch)
    cluster_hash = _add_cluster('c')

    for reason, event_type, ts in [
        ('Provisioning', global_user_state.ClusterEventType.STATUS_CHANGE, 100),
        ('Launching (pulling)',
         global_user_state.ClusterEventType.LAUNCH_PROGRESS, 200),
        ('Cluster provisioned',
         global_user_state.ClusterEventType.STATUS_CHANGE, 300),
            # A DEBUG row that must be excluded by the type filter.
        ('debug-noise', global_user_state.ClusterEventType.DEBUG, 250),
    ]:
        global_user_state.add_cluster_event('c',
                                            new_status=None,
                                            reason=reason,
                                            event_type=event_type,
                                            transitioned_at=ts)

    both = [
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    ]
    # Merged across both types, oldest-to-newest, DEBUG excluded.
    assert global_user_state.get_cluster_events(cluster_name=None,
                                                cluster_hash=cluster_hash,
                                                event_type=both) == [
                                                    'Provisioning',
                                                    'Launching (pulling)',
                                                    'Cluster provisioned'
                                                ]
    # A single type still behaves as before.
    assert global_user_state.get_cluster_events(
        cluster_name=None,
        cluster_hash=cluster_hash,
        event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS) == [
            'Launching (pulling)'
        ]
    # limit keeps the most recent N across the combined set (still ASC).
    assert global_user_state.get_cluster_events(
        cluster_name=None, cluster_hash=cluster_hash, event_type=both,
        limit=2) == ['Launching (pulling)', 'Cluster provisioned']


def test_latest_cluster_events_batched(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    for name in ('c-a', 'c-b', 'c-c'):
        _add_cluster(name)
    progress = global_user_state.ClusterEventType.LAUNCH_PROGRESS
    # Explicit transitioned_at: no sleeping for the 1s timestamp resolution.
    global_user_state.add_cluster_event('c-a',
                                        None,
                                        'Launching (pending: Resources)',
                                        progress,
                                        transitioned_at=1000)
    global_user_state.add_cluster_event('c-a',
                                        None,
                                        'Launching (pending: QOSGrpGRES)',
                                        progress,
                                        transitioned_at=2000)
    # A different type on c-b must not be picked up by a progress-only query.
    global_user_state.add_cluster_event(
        'c-b',
        status_lib.ClusterStatus.INIT,
        'init',
        global_user_state.ClusterEventType.STATUS_CHANGE,
        transitioned_at=1500)

    events = global_user_state.get_latest_cluster_events(
        ['c-a', 'c-b', 'c-c', 'missing'], [progress])
    assert events == {'c-a': ('Launching (pending: QOSGrpGRES)', 2000)}
    # Both types requested: c-b's status change is returned with its stamp.
    both = global_user_state.get_latest_cluster_events(
        ['c-a', 'c-b'],
        [progress, global_user_state.ClusterEventType.STATUS_CHANGE])
    assert both == {
        'c-a': ('Launching (pending: QOSGrpGRES)', 2000),
        'c-b': ('init', 1500),
    }
    assert not global_user_state.get_latest_cluster_events([], [progress])
    assert not global_user_state.get_latest_cluster_events(['c-a'], [])


def test_latest_cluster_events_ties_are_stable(tmp_path, monkeypatch):
    """transitioned_at is whole seconds and the table has no insertion order,
    so two events written inside one second need a second sort key for the
    answer to be stable at all. *Which* of the two wins is the database's
    collation, so it is deliberately not asserted -- see the ordering comment
    in get_latest_cluster_events.
    """
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c-a')
    progress = global_user_state.ClusterEventType.LAUNCH_PROGRESS
    reasons = {'Launching (one thing)', 'Launching (another thing)'}
    for reason in reasons:
        global_user_state.add_cluster_event('c-a',
                                            None,
                                            reason,
                                            progress,
                                            transitioned_at=1000)
    seen = {
        global_user_state.get_latest_cluster_events(['c-a'], [progress])['c-a']
        for _ in range(5)
    }
    assert len(seen) == 1
    reason, transitioned_at = seen.pop()
    assert reason in reasons
    assert transitioned_at == 1000


def test_latest_cluster_events_chunks_the_name_list(tmp_path, monkeypatch):
    """SQLite caps a statement at 999 bound parameters. Unchunked, a
    deployment with that many clusters provisioning at once raised -- and the
    caller swallows the error, so *every* cluster lost its launch reason
    rather than the excess.
    """
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE', 2)
    progress = global_user_state.ClusterEventType.LAUNCH_PROGRESS
    names = []
    for i in range(5):
        name = f'chunk-{i}'
        _add_cluster(name)
        names.append(name)
        global_user_state.add_cluster_event(name,
                                            None,
                                            f'Launching (pending: r{i})',
                                            progress,
                                            transitioned_at=1000 + i)
    events = global_user_state.get_latest_cluster_events(names, [progress])
    # Every cluster answered, across three batches of at most two names.
    assert events == {
        f'chunk-{i}': (f'Launching (pending: r{i})', 1000 + i) for i in range(5)
    }
