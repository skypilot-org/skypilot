"""Tests for multi-try provision log path tracking in cluster history.

A managed-job recovery re-launches the same cluster name — either in place
(same cluster_hash, so the history row is reused) or after a terminate (new
cluster_hash, new history row). The scalar provision_log_path column only
ever held the latest try, so the pre-recovery try's provision log was
unrecoverable from the DB. These tests cover the append-only
provision_log_paths list and the getter the debug dump uses to collect every
try.
"""
import json

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


def _add_cluster(name: str, provision_log_path=None, ready=False) -> None:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=ready,
        provision_log_path=provision_log_path,
    )


def _history_row(name: str):
    engine = global_user_state._db_manager.get_engine()
    from sqlalchemy import orm  # pylint: disable=import-outside-toplevel
    with orm.Session(engine) as session:
        return session.query(
            global_user_state.cluster_history_table).filter_by(
                name=name).first()


def test_launch_records_path_in_scalar_and_list(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')

    row = _history_row('c')
    assert row.provision_log_path == '/logs/try1/provision.log'
    assert json.loads(row.provision_log_paths) == [
        '/logs/try1/provision.log'
    ]
    assert global_user_state.get_cluster_history_provision_log_paths('c') == [
        '/logs/try1/provision.log'
    ]


def test_post_provision_update_does_not_null_path(tmp_path, monkeypatch):
    """add_or_update_cluster is called again after provisioning completes
    (ready=True) without a provision_log_path; the recorded path must
    survive rather than being overwritten with None."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')
    _add_cluster('c', ready=True)  # post-provision update, no path

    row = _history_row('c')
    assert row.provision_log_path == '/logs/try1/provision.log'
    assert json.loads(row.provision_log_paths) == [
        '/logs/try1/provision.log'
    ]


def test_in_place_relaunch_appends_new_try(tmp_path, monkeypatch):
    """A recovery re-launch of a live cluster reuses the cluster_hash; the
    new try's path must append, keeping the pre-recovery try."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')
    _add_cluster('c', ready=True)
    _add_cluster('c', provision_log_path='/logs/try2/provision.log')

    row = _history_row('c')
    assert row.provision_log_path == '/logs/try2/provision.log'
    assert json.loads(row.provision_log_paths) == [
        '/logs/try1/provision.log', '/logs/try2/provision.log'
    ]
    assert global_user_state.get_cluster_history_provision_log_paths('c') == [
        '/logs/try1/provision.log', '/logs/try2/provision.log'
    ]


def test_terminate_and_relaunch_spans_incarnations(tmp_path, monkeypatch):
    """A terminate-then-relaunch creates a new history row; the getter must
    return the paths of every incarnation, oldest first."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')
    global_user_state.remove_cluster('c', terminate=True)
    _add_cluster('c', provision_log_path='/logs/try2/provision.log')

    assert global_user_state.get_cluster_history_provision_log_paths('c') == [
        '/logs/try1/provision.log', '/logs/try2/provision.log'
    ]


def test_relaunch_with_same_path_does_not_duplicate(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')
    _add_cluster('c', provision_log_path='/logs/try1/provision.log')

    row = _history_row('c')
    assert json.loads(row.provision_log_paths) == [
        '/logs/try1/provision.log'
    ]


def test_merge_folds_legacy_scalar_into_list():
    """Rows written before the list column existed have only the scalar; a
    later merge must not lose it."""
    latest, paths_json = global_user_state._merge_provision_log_paths(
        '/logs/old/provision.log', None, '/logs/new/provision.log')
    assert latest == '/logs/new/provision.log'
    assert json.loads(paths_json) == [
        '/logs/old/provision.log', '/logs/new/provision.log'
    ]


def test_merge_preserves_on_none_new_path():
    latest, paths_json = global_user_state._merge_provision_log_paths(
        '/logs/old/provision.log', json.dumps(['/logs/old/provision.log']),
        None)
    assert latest == '/logs/old/provision.log'
    assert json.loads(paths_json) == ['/logs/old/provision.log']


def test_merge_tolerates_corrupt_json():
    latest, paths_json = global_user_state._merge_provision_log_paths(
        None, 'not-json', '/logs/new/provision.log')
    assert latest == '/logs/new/provision.log'
    assert json.loads(paths_json) == ['/logs/new/provision.log']


def test_merge_caps_retained_paths():
    existing = [f'/logs/try{i}/provision.log' for i in range(25)]
    latest, paths_json = global_user_state._merge_provision_log_paths(
        None, json.dumps(existing), '/logs/new/provision.log')
    paths = json.loads(paths_json)
    assert len(paths) == global_user_state._MAX_PROVISION_LOG_PATHS
    assert paths[-1] == '/logs/new/provision.log'
    # Oldest entries are the ones evicted.
    assert '/logs/try0/provision.log' not in paths
