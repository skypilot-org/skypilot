"""Unit tests for global_user_state.get_cluster_owner_names."""
from sqlalchemy import orm

from sky import global_user_state
from sky import models
from sky.skylet import constants
from sky.utils.db import db_utils

_SERVER = models.User(id='aaaa1111', name='server-admin')
_ALICE = models.User(id='bbbb2222', name='alice')


class _MinimalHandle:
    launched_resources = None


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


def _add_cluster(name, user_hash):
    global_user_state.add_or_update_cluster(cluster_name=name,
                                            cluster_handle=_MinimalHandle(),
                                            requested_resources=set(),
                                            ready=True)
    engine = global_user_state._db_manager.get_engine()
    with orm.Session(engine) as session:
        session.query(global_user_state.cluster_table).filter_by(
            name=name).update({'user_hash': user_hash})
        session.commit()


def test_owner_is_never_replaced_by_the_current_user(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    # The user get_clusters() falls back to: the API server's own.
    monkeypatch.setenv(constants.USER_ID_ENV_VAR, _SERVER.id)
    global_user_state.add_or_update_user(_SERVER)
    global_user_state.add_or_update_user(_ALICE)
    _add_cluster('owned', _ALICE.id)
    _add_cluster('no-owner', None)
    _add_cluster('owner-gone', 'cccc3333')

    shown = {
        record['name']: record['user_name']
        for record in global_user_state.get_clusters()
    }
    assert shown == {
        'owned': 'alice',
        'no-owner': 'server-admin',
        'owner-gone': 'server-admin',
    }
    assert global_user_state.get_cluster_owner_names() == {
        'owned': 'alice',
        'no-owner': None,
        'owner-gone': None,
    }


def test_cluster_of_the_current_user_keeps_its_owner(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setenv(constants.USER_ID_ENV_VAR, _SERVER.id)
    global_user_state.add_or_update_user(_SERVER)
    _add_cluster('local', _SERVER.id)

    assert global_user_state.get_cluster_owner_names() == {
        'local': 'server-admin'
    }
