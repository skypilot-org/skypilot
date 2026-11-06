"""The cluster record builders in global_user_state must agree on shape.

A status refresh replaces each record from get_clusters with one from
get_cluster_from_name, so a field that only the list builder fills silently
disappears from refreshed responses (e.g. node_names).
"""
import pytest
import sqlalchemy
from sqlalchemy import orm

from sky import global_user_state
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.db import db_utils
from sky.utils.db import retries as db_retries


@pytest.fixture
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


class _ClusterInfo:

    def get_node_names(self):
        return ['node-a', 'node-b']


class _Resources:
    priority = 7
    priority_class = 'pc-a'


class _Handle:
    launched_resources = _Resources()
    cached_cluster_info = _ClusterInfo()


_LINKS = {'console': 'https://example.com/c'}
_ABNORMAL = f'{global_user_state.ABNORMAL_STATUS_REASON_PREFIX} a node is down.'


def _add_cluster(name: str, state: str) -> None:
    """state: 'up', 'launching' (new INIT) or 'unhealthy' (INIT by refresh)."""
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_Handle(),
        requested_resources=set(),
        ready=state == 'up',
    )
    if state == 'unhealthy':
        global_user_state.add_cluster_event(
            name, status_lib.ClusterStatus.INIT, _ABNORMAL,
            global_user_state.ClusterEventType.STATUS_CHANGE)
    with orm.Session(global_user_state._db_manager.get_engine()) as session:
        session.execute(global_user_state.cluster_table.update().where(
            global_user_state.cluster_table.c.name == name).values(
                links=_LINKS))
        session.commit()


_SHARED = ('node_names', 'priority', 'priority_class', 'init_kind',
           'init_status_reason')
_EXPECTED_INIT = {
    'up': (None, None),
    'launching': (status_lib.INIT_KIND_LAUNCHING, None),
    'unhealthy': (status_lib.INIT_KIND_UNHEALTHY, _ABNORMAL),
}


@pytest.mark.parametrize('state', ['up', 'launching', 'unhealthy'])
@pytest.mark.parametrize('summary_response', [True, False])
def test_single_record_carries_every_list_field(_fresh_db, state,
                                                summary_response):
    _add_cluster('c', state)
    listed = global_user_state.get_clusters(
        summary_response=summary_response)[0]
    single = global_user_state.get_cluster_from_name(
        'c', summary_response=summary_response)

    assert set(listed) - set(single) == set()
    for key in _SHARED:
        assert single[key] == listed[key], key
    assert (single['node_names'], single['priority'],
            single['priority_class']) == ('node-a,node-b', 7, 'pc-a')
    assert (single['init_kind'],
            single['init_status_reason']) == _EXPECTED_INIT[state]
    if not summary_response:
        assert single['links'] == listed['links'] == _LINKS


@pytest.mark.parametrize('state', ['up', 'launching', 'unhealthy'])
def test_batched_records_match_single_summary_shape(_fresh_db, state):
    _add_cluster('c', state)
    single = global_user_state.get_cluster_from_name('c', summary_response=True)
    batched = global_user_state.get_clusters_from_names(
        ['c'], include_user_info=True)['c']

    assert set(batched) == set(single)
    for key in _SHARED:
        assert batched[key] == single[key], key
    assert (batched['init_kind'],
            batched['init_status_reason']) == _EXPECTED_INIT[state]


def test_get_cluster_from_name_retries_transient_db_errors(
        _fresh_db, monkeypatch):
    """Callers such as the jobs controller rely on its transient-DB retry."""
    _add_cluster('c', 'up')
    manager = global_user_state._db_manager
    real_get_engine = manager.get_engine
    failures = [sqlalchemy.exc.OperationalError('SELECT', {}, Exception())]

    def _flaky_get_engine():
        if failures:
            raise failures.pop()
        return real_get_engine()

    monkeypatch.setattr(manager, 'get_engine', _flaky_get_engine)
    monkeypatch.setattr(db_retries.time, 'sleep', lambda _: None)
    assert global_user_state.get_cluster_from_name(
        'c')['node_names'] == 'node-a,node-b'
    assert not failures
