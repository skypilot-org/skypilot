"""Tests for per-host skylet tunnel bookkeeping."""
import json
import pickle
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import sqlalchemy
from sqlalchemy import orm

from sky import global_user_state
from sky.backends import backend_utils
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.backends.cloud_vm_ray_backend import SSHTunnelInfo
from sky.skylet import constants
from sky.utils import locks
from sky.utils.db import db_utils

_CLUSTER = 'test-cluster'
_GRPC_PORT = constants.SKYLET_GRPC_PORT


class _MinimalHandle:
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
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
    global_user_state.add_or_update_cluster(cluster_name=_CLUSTER,
                                            cluster_handle=_MinimalHandle(),
                                            requested_resources=set(),
                                            ready=False)


def _as_owner(monkeypatch, owner_id: str) -> None:
    monkeypatch.setattr(backend_utils, 'skylet_tunnel_owner_id',
                        lambda: owner_id)


def _handle() -> CloudVmRayResourceHandle:
    return CloudVmRayResourceHandle(cluster_name=_CLUSTER,
                                    cluster_name_on_cloud=f'{_CLUSTER}-abc',
                                    cluster_yaml=None,
                                    launched_nodes=1,
                                    launched_resources=MagicMock())


def _write_released_version_tunnel(value) -> None:
    """Writes the old column the way released versions do."""
    engine = global_user_state._db_manager.get_engine()
    with orm.Session(engine) as session:
        session.query(
            global_user_state.cluster_table).filter_by(name=_CLUSTER).update({
                global_user_state.cluster_table.c.skylet_ssh_tunnel_metadata:
                    pickle.dumps(value)
            })
        session.commit()


def _read_raw_columns():
    """Returns both columns as stored, without the ORM's JSON decoding."""
    engine = global_user_state._db_manager.get_engine()
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.text('SELECT skylet_ssh_tunnel_metadata, '
                            'skylet_ssh_tunnels FROM clusters '
                            'WHERE name = :name'), {
                                'name': _CLUSTER
                            }).one()


def test_owners_keep_independent_entries(fresh_db):
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a',
                                                    (10000, 111))
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-b',
                                                    (20000, 222))

    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-a') == (10000,
                                                                         111)
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-b') == (20000,
                                                                         222)
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-c') is None


def test_setting_none_removes_only_that_owner(fresh_db):
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a',
                                                    (10000, 111))
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-b',
                                                    (20000, 222))

    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a', None)

    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-a') is None
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-b') == (20000,
                                                                         222)


def test_set_on_missing_cluster_raises(fresh_db):
    with pytest.raises(ValueError):
        global_user_state.set_cluster_skylet_ssh_tunnel('no-such-cluster',
                                                        'host-a', (10000, 111))


def test_released_version_tunnel_is_ignored_and_left_untouched(fresh_db):
    _write_released_version_tunnel((10000, 111))
    old_value, new_value = _read_raw_columns()
    assert new_value is None

    for owner in ('host-a', 'host-b'):
        assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                               owner) is None

    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-b',
                                                    (20000, 222))
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a',
                                                    (30000, 333))
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a', None)

    after_old_value, after_new_value = _read_raw_columns()
    assert after_old_value == old_value
    assert pickle.loads(after_old_value) == (10000, 111)
    assert json.loads(after_new_value) == {'host-b': [20000, 222]}
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-b') == (20000,
                                                                         222)

    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-b', None)

    cleared_old_value, cleared_new_value = _read_raw_columns()
    assert cleared_old_value == old_value
    assert cleared_new_value is None


def test_close_only_touches_this_hosts_entry(fresh_db, monkeypatch):
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a',
                                                    (10000, 111))
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-b',
                                                    (20000, 222))
    _as_owner(monkeypatch, 'host-a')
    handle = _handle()

    with patch.object(handle, '_terminate_ssh_tunnel_process') as terminate:
        handle.close_skylet_ssh_tunnel()

    terminate.assert_called_once_with(SSHTunnelInfo(port=10000, pid=111))
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-a') is None
    assert global_user_state.get_cluster_skylet_ssh_tunnel(_CLUSTER,
                                                           'host-b') == (20000,
                                                                         222)


def test_get_grpc_channel_ignores_other_hosts_tunnel(fresh_db, monkeypatch):
    global_user_state.set_cluster_skylet_ssh_tunnel(_CLUSTER, 'host-a',
                                                    (10000, 111))
    _as_owner(monkeypatch, 'host-b')
    handle = _handle()
    own_tunnel = SSHTunnelInfo(port=20000, pid=222)

    exclusive_lock = MagicMock()
    with patch.object(handle, '_open_and_update_skylet_tunnel',
                      return_value=own_tunnel) as open_tunnel, \
            patch.object(locks, 'get_lock',
                         return_value=exclusive_lock) as get_lock, \
            patch('grpc.insecure_channel') as channel, \
            patch('socket.socket') as mock_socket:
        # Every port accepts connections, so host-a's entry would look
        # healthy if host-b read it.
        mock_socket.return_value.__enter__.return_value.connect.return_value = (
            None)

        assert handle.get_grpc_channel() == channel.return_value

    open_tunnel.assert_called_once_with()
    assert channel.call_args.args[0] == 'localhost:20000'
    assert get_lock.call_args.args[0] == f'{_CLUSTER}_host-b_ssh_tunnel'


def test_cluster_tunnel_lock_id_is_per_owner(monkeypatch):
    _as_owner(monkeypatch, 'host-a')
    lock_a = backend_utils.cluster_tunnel_lock_id(_CLUSTER)
    _as_owner(monkeypatch, 'host-b')
    lock_b = backend_utils.cluster_tunnel_lock_id(_CLUSTER)
    assert lock_a == f'{_CLUSTER}_host-a_ssh_tunnel'
    assert lock_a != lock_b
