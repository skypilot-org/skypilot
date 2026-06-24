"""Tests that cluster history can filter out controller-backed clusters.

Clusters that back managed jobs/services are recorded with is_managed=1.
`sky.core.status` already hides them from the active cluster list, but the
cluster history (which powers the dashboard's "Show history" / cost report
view) reads the cluster_history table. That table now carries its own
is_managed column so these clusters stay hidden even after they are
terminated and their clusters-table row is gone (the history join is a LEFT
OUTER JOIN, so the flag can no longer be sourced from the clusters table at
that point).
"""
from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
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
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None


def _add_cluster(name: str, is_managed: bool) -> None:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
        is_managed=is_managed,
    )


def _history_names(exclude_managed_clusters: bool):
    return {
        r['name'] for r in global_user_state.get_clusters_from_history(
            exclude_managed_clusters=exclude_managed_clusters)
    }


def test_history_includes_managed_by_default(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('regular', is_managed=False)
    _add_cluster('my-job-1', is_managed=True)

    # Default behavior is unchanged: both clusters are reported.
    assert _history_names(exclude_managed_clusters=False) == {
        'regular', 'my-job-1'
    }


def test_history_excludes_managed_when_requested(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('regular', is_managed=False)
    _add_cluster('my-job-1', is_managed=True)

    assert _history_names(exclude_managed_clusters=True) == {'regular'}


def test_history_managed_flag_not_reset_on_update(tmp_path, monkeypatch):
    """add_or_update_cluster is called multiple times during a managed-job
    launch, and is_managed defaults to False on the later calls. The flag
    recorded on the first call must not be overwritten, otherwise the managed
    cluster would leak back into the history view.
    """
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('my-job-1', is_managed=True)
    # Subsequent update with the default is_managed=False must not clear it.
    _add_cluster('my-job-1', is_managed=False)

    assert _history_names(exclude_managed_clusters=True) == set()
    assert _history_names(exclude_managed_clusters=False) == {'my-job-1'}


def test_history_excludes_managed_after_termination(tmp_path, monkeypatch):
    """The bug scenario: a terminated managed-job cluster must stay hidden.

    Once the cluster is terminated its clusters-table row is deleted, so the
    is_managed flag can only come from the cluster_history table.
    """
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('regular', is_managed=False)
    _add_cluster('my-job-1', is_managed=True)

    global_user_state.remove_cluster('regular', terminate=True)
    global_user_state.remove_cluster('my-job-1', terminate=True)

    # Both are now history-only (no clusters-table row).
    assert _history_names(exclude_managed_clusters=False) == {
        'regular', 'my-job-1'
    }
    assert _history_names(exclude_managed_clusters=True) == {'regular'}
