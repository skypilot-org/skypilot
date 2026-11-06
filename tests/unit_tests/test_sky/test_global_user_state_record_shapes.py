"""The cluster record builders in global_user_state must agree on shape.

A status refresh replaces each record from get_clusters with one from
get_cluster_from_name, so a field that only the list builder fills silently
disappears from refreshed responses (e.g. node_names).
"""
import pytest

from sky import global_user_state
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.db import db_utils


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


class _Handle:
    launched_resources = None
    cached_cluster_info = _ClusterInfo()


def _add_cluster(name: str, ready: bool) -> None:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_Handle(),
        requested_resources=set(),
        ready=ready,
    )


@pytest.mark.parametrize('ready', [True, False], ids=['up', 'init'])
@pytest.mark.parametrize('summary_response', [True, False])
def test_single_record_carries_every_list_field(_fresh_db, ready,
                                                summary_response):
    _add_cluster('c', ready)
    listed = global_user_state.get_clusters(
        summary_response=summary_response)[0]
    single = global_user_state.get_cluster_from_name(
        'c', summary_response=summary_response)

    assert set(listed) - set(single) == set()
    for key in ('node_names', 'priority', 'priority_class', 'init_kind',
                'init_status_reason'):
        assert single[key] == listed[key], key
    assert single['node_names'] == 'node-a,node-b'
    expected_init = None if ready else status_lib.INIT_KIND_LAUNCHING
    assert single['init_kind'] == expected_init


@pytest.mark.parametrize('ready', [True, False], ids=['up', 'init'])
def test_batched_records_match_single_summary_shape(_fresh_db, ready):
    _add_cluster('c', ready)
    single = global_user_state.get_cluster_from_name('c', summary_response=True)
    batched = global_user_state.get_clusters_from_names(
        ['c'], include_user_info=True)['c']

    assert set(batched) == set(single)
    for key in ('node_names', 'priority', 'priority_class', 'init_kind',
                'init_status_reason'):
        assert batched[key] == single[key], key
