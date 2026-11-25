"""Tests for the usage intervals that stopping and tearing down record."""
import sqlalchemy
from sqlalchemy import orm

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


class _Clock:

    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


class _MinimalHandle:
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None


def _fresh_db(tmp_path, monkeypatch) -> _Clock:
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
    clock = _Clock(1_000_000)
    monkeypatch.setattr(global_user_state.time, 'time', clock)
    return clock


def _launch(name: str) -> None:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
    )


def _history(name: str):
    cluster_hash = global_user_state._get_hash_for_existing_cluster(name)
    table = global_user_state.cluster_history_table
    with orm.Session(global_user_state._db_manager.get_engine()) as session:
        last_activity_time = session.execute(
            sqlalchemy.select(table.c.last_activity_time).where(
                table.c.cluster_hash == cluster_hash)).scalar_one()
    return (global_user_state._get_cluster_usage_intervals(cluster_hash),
            last_activity_time)


def test_stop_closes_the_running_interval(tmp_path, monkeypatch):
    clock = _fresh_db(tmp_path, monkeypatch)
    _launch('c')
    clock.now += 100
    global_user_state.remove_cluster('c', terminate=False)

    assert _history('c') == ([(1_000_000, 1_000_100)], 1_000_100)


def test_stopped_cluster_does_not_accrue_usage(tmp_path, monkeypatch):
    # Status refresh records a still-stopped cluster as stopped again.
    clock = _fresh_db(tmp_path, monkeypatch)
    _launch('c')
    clock.now += 100
    global_user_state.remove_cluster('c', terminate=False)
    clock.now += 3600
    global_user_state.remove_cluster('c', terminate=False)

    assert _history('c') == ([(1_000_000, 1_000_100)], 1_000_100)


def test_teardown_of_stopped_cluster_keeps_its_usage(tmp_path, monkeypatch):
    clock = _fresh_db(tmp_path, monkeypatch)
    _launch('c')
    clock.now += 100
    global_user_state.remove_cluster('c', terminate=False)
    cluster_hash = global_user_state._get_hash_for_existing_cluster('c')
    clock.now += 2 * 24 * 3600
    global_user_state.remove_cluster('c', terminate=True)

    assert global_user_state._get_cluster_usage_intervals(cluster_hash) == [
        (1_000_000, 1_000_100)
    ]
    # The teardown still counts as recent activity for history views.
    history = global_user_state.get_clusters_from_history(days=1)
    assert [r['name'] for r in history] == ['c']


def test_restart_after_stop_opens_a_new_interval(tmp_path, monkeypatch):
    clock = _fresh_db(tmp_path, monkeypatch)
    _launch('c')
    clock.now += 100
    global_user_state.remove_cluster('c', terminate=False)
    clock.now += 1000
    _launch('c')
    clock.now += 50
    global_user_state.remove_cluster('c', terminate=True)

    cluster_hash = global_user_state.get_clusters_from_history(
    )[0]['cluster_hash']
    assert global_user_state._get_cluster_usage_intervals(cluster_hash) == [
        (1_000_000, 1_000_100), (1_001_100, 1_001_150)
    ]
