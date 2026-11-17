"""Storage listing, deletion and name completion against the real
permission service: a real state DB and casbin policy, as deployed."""
import asyncio
import json
import os
from unittest import mock

import casbin
import fastapi
import pytest
import sqlalchemy_adapter

import sky
from sky import core
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.data import storage as storage_lib
from sky.jobs.server import core as jobs_core
from sky.server import server
from sky.skylet import constants
from sky.users import permission
from sky.utils import annotations
from sky.utils import locks
from sky.utils import status_lib
from sky.utils.db import db_utils
from sky.utils.db import kv_cache

_WORKSPACES = {
    'ws-a': {
        'private': True,
        'allowed_users': ['alice']
    },
    'ws-ro': {
        'private': True,
        'allowed_users': ['alice'],
        'read_access': 'all'
    },
}


def _user(user_id):
    return models.User(id=user_id, name=user_id)


def _add(name, user_id, workspace):
    handle = storage_lib.Storage.StorageMetadata(storage_name=name,
                                                 source=f's3://{name}')
    with skypilot_config.local_active_workspace_ctx(workspace), \
         mock.patch('sky.utils.common_utils.get_current_user',
                    return_value=_user(user_id)):
        global_user_state.add_or_update_storage(name, handle,
                                                status_lib.StorageStatus.READY)


@pytest.fixture
def storages(tmp_path, monkeypatch):
    """alice is the only member of private ws-a and of private ws-ro, which
    non-members may read; bob is a plain user and admin an admin. Storage
    st-a lives in ws-a, st-ro in ws-ro and st-d in the default workspace,
    all created by alice."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.delenv(constants.ENV_VAR_DB_CONNECTION_URI, raising=False)
    # Another test in the same worker may leak ENV_VAR_SKYPILOT_CONFIG into
    # os.environ, which takes priority over ENV_VAR_GLOBAL_CONFIG.
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    config = tmp_path / 'config.yaml'
    config.write_text(json.dumps({'workspaces': _WORKSPACES}))
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, str(config))
    skypilot_config.reload_config()
    annotations.clear_request_level_cache()
    state_db = db_utils.DatabaseManager('state', global_user_state.create_table)
    monkeypatch.setattr(global_user_state, '_db_manager', state_db)
    monkeypatch.setattr(
        kv_cache, '_db_manager',
        db_utils.DatabaseManager('kv_cache', kv_cache.create_table))
    service = permission.PermissionService()
    service.enforcer = casbin.SyncedEnforcer(
        os.path.join(os.path.dirname(permission.__file__), 'model.conf'),
        sqlalchemy_adapter.Adapter(state_db.get_engine(),
                                   db_class=sqlalchemy_adapter.CasbinRule))
    monkeypatch.setattr(permission, 'permission_service', service)
    for user_id in ['admin', 'alice', 'bob']:
        service.enforcer.add_grouping_policy(
            user_id, 'admin' if user_id == 'admin' else 'user')
    for user_id, workspace in [('*', 'default'), ('alice', 'ws-a'),
                               ('alice', 'ws-ro')]:
        service.enforcer.add_policy(user_id, workspace, '*')

    _add('st-a', 'alice', 'ws-a')
    _add('st-ro', 'alice', 'ws-ro')
    _add('st-d', 'alice', constants.SKYPILOT_DEFAULT_WORKSPACE)
    yield config, service
    annotations.clear_request_level_cache()


def _as(user_id):
    return mock.patch('sky.utils.common_utils.get_current_user',
                      return_value=_user(user_id))


def _delete(user_id, name):
    """Deletes as ``user_id``; the bucket deletion itself is stubbed."""
    with _as(user_id), \
         mock.patch('sky.core.data.Storage.from_handle') as from_handle:
        core.storage_delete(name)
    from_handle.return_value.delete.assert_called_once_with()


def _listed(user_id):
    with _as(user_id):
        return sorted(record.name for record in core.storage_ls())


def _request(user_id):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.auth_user = None if user_id is None else _user(user_id)
    return request


def _completed(user_id, incomplete='st'):
    return sorted(
        asyncio.run(server.complete_storage_name(_request(user_id),
                                                 incomplete)))


@pytest.mark.usefixtures('storages')
class TestStorageDelete:
    """Deleting needs write access to the storage's workspace."""

    def test_read_only_workspace_is_not_deletable_by_a_non_member(self):
        with _as('bob'), pytest.raises(exceptions.PermissionDeniedError):
            core.storage_delete('st-ro')

    def test_unreadable_workspace_reads_as_missing(self):
        with _as('bob'), pytest.raises(ValueError, match='not found'):
            core.storage_delete('st-a')

    def test_member_deletes(self):
        _delete('alice', 'st-ro')
        _delete('alice', 'st-a')

    def test_admin_deletes_anything(self):
        _delete('admin', 'st-a')

    def test_anyone_deletes_in_the_default_workspace(self):
        _delete('bob', 'st-d')

    def test_admin_deletes_scratch_storage_of_a_removed_workspace(self):
        """Builds the storage from its record for real, so the store's own
        name check runs too; only the cloud calls are stubbed."""
        metadata = storage_lib.S3Store.StoreMetadata(name='st-gone',
                                                     source=None,
                                                     region='us-east-1',
                                                     is_sky_managed=True)
        handle = storage_lib.Storage.StorageMetadata(
            storage_name='st-gone',
            source=None,
            sky_stores={storage_lib.StoreType.S3: metadata})
        with skypilot_config.local_active_workspace_ctx('ws-gone'), \
             _as('alice'):
            global_user_state.add_or_update_storage(
                'st-gone', handle, status_lib.StorageStatus.READY)
        client = mock.MagicMock()
        with _as('admin'), \
             mock.patch('sky.data.storage.common_utils.is_in_request_context',
                        return_value=True), \
             mock.patch('sky.adaptors.aws.client', return_value=client), \
             mock.patch('sky.adaptors.aws.resource', return_value=client), \
             mock.patch('sky.data.storage._is_storage_cloud_enabled',
                        return_value=True), \
             mock.patch.object(storage_lib.S3Store, 'delete') as store_delete:
            core.storage_delete('st-gone')
        store_delete.assert_called_once_with()
        assert global_user_state.get_storage_by_name('st-gone') is None


@pytest.mark.usefixtures('storages')
class TestStorageListing:
    """Listing shows the workspaces the caller can read."""

    def test_non_member_sees_readable_workspaces_only(self):
        assert _listed('bob') == ['st-d', 'st-ro']

    def test_member_sees_their_workspaces(self):
        assert _listed('alice') == ['st-a', 'st-d', 'st-ro']

    def test_admin_sees_everything(self):
        assert _listed('admin') == ['st-a', 'st-d', 'st-ro']


def _reuse(user_id, name):
    with _as(user_id), \
         mock.patch('sky.data.storage.common_utils.is_in_request_context',
                    return_value=True):
        return storage_lib._get_accessible_handle(name)  # pylint: disable=protected-access


@pytest.mark.usefixtures('storages')
class TestStorageReuse:
    """Reusing a storage by name in a task needs write access to its
    workspace, the same boundary as deleting it."""

    def test_non_member_of_a_read_only_workspace_is_refused(self):
        with pytest.raises(exceptions.PermissionDeniedError, match='read-only'):
            _reuse('bob', 'st-ro')

    def test_unreadable_workspace_reads_as_taken(self):
        with pytest.raises(exceptions.StorageWorkspaceError,
                           match='is taken by a storage in a workspace you '
                           'cannot access'):
            _reuse('bob', 'st-a')

    def test_member_reuses(self):
        assert _reuse('alice', 'st-ro').storage_name == 'st-ro'
        assert _reuse('alice', 'st-a').storage_name == 'st-a'

    def test_admin_reuses_anything(self):
        assert _reuse('admin', 'st-a').storage_name == 'st-a'

    def test_anyone_reuses_in_the_default_workspace(self):
        assert _reuse('bob', 'st-d').storage_name == 'st-d'

    def test_storage_of_a_removed_workspace_is_the_admins_only(self):
        _add('st-gone', 'alice', 'ws-gone')
        assert _reuse('admin', 'st-gone').storage_name == 'st-gone'
        with pytest.raises(exceptions.StorageWorkspaceError, match='taken'):
            _reuse('alice', 'st-gone')


class _Proceeded(Exception):
    """Raised by the first step after the storage check, to stop there."""


def _launch_naming(user_id,
                   name,
                   workspace=constants.SKYPILOT_DEFAULT_WORKSPACE,
                   persistent=False):
    """Submits a managed job as ``user_id``, with ``workspace`` active, with
    a mount of the storage ``name``, stopping right after the storage
    check."""
    task = sky.Task(run='echo')
    task.set_storage_mounts(
        {'/data': storage_lib.Storage(name=name, persistent=persistent)})
    with _as(user_id), skypilot_config.local_active_workspace_ctx(workspace), \
         mock.patch('sky.data.storage.common_utils.is_in_request_context',
                    return_value=True), \
         mock.patch('sky.jobs.server.core.clouds.Kubernetes.'
                    'check_request_keeps_server_none', side_effect=_Proceeded):
        jobs_core._launch(task,
                          name=None,
                          pool=None,
                          num_jobs=None,
                          stream_logs=False,
                          file_mounts_blob_id=None,
                          parent_job_id=None,
                          parent_task_id=None,
                          root_job_id=None,
                          depends_on=[])


@pytest.mark.usefixtures('storages')
class TestJobsLaunch:
    """`jobs launch` checks every storage the task names in the request,
    whichever of its upload branches runs; the controller, which may be
    the first to construct the storage, is outside any request and runs
    for the workspace the job was launched in."""

    def test_unreadable_workspace_reads_as_taken(self):
        with pytest.raises(exceptions.StorageWorkspaceError, match='is taken'):
            _launch_naming('bob', 'st-a')

    def test_read_only_workspace_is_refused(self):
        with pytest.raises(exceptions.PermissionDeniedError, match='read-only'):
            _launch_naming('bob', 'st-ro')

    def test_writable_workspace_other_than_the_active_one_is_refused(self):
        for user_id in ('alice', 'admin'):
            with pytest.raises(exceptions.StorageWorkspaceError,
                               match="'st-a' is in workspace 'ws-a', not in "
                               "the active workspace 'default'"):
                _launch_naming(user_id, 'st-a')

    def test_persistent_mount_of_a_writable_workspace_proceeds(self):
        for user_id in ('alice', 'admin'):
            with pytest.raises(_Proceeded):
                _launch_naming(user_id, 'st-a', persistent=True)

    def test_storage_of_the_active_workspace_proceeds(self):
        for user_id, name, workspace in [
            ('alice', 'st-a', 'ws-a'), ('admin', 'st-a', 'ws-a'),
            ('bob', 'st-d', constants.SKYPILOT_DEFAULT_WORKSPACE),
            ('bob', 'brand-new', constants.SKYPILOT_DEFAULT_WORKSPACE)
        ]:
            with pytest.raises(_Proceeded):
                _launch_naming(user_id, name, workspace)


def _reuse_on_controller(workspace, name):
    """The lookup as a controller running for ``workspace`` makes it."""
    with skypilot_config.local_active_workspace_ctx(workspace), \
         mock.patch('sky.data.storage.common_utils.is_in_request_context',
                    return_value=False):
        return storage_lib._get_accessible_handle(name)  # pylint: disable=protected-access


@pytest.mark.usefixtures('storages')
class TestOnTheController:
    """A controller runs for one workspace and tears down that workspace's
    storage; another workspace's reads as missing, whoever submitted the
    job."""

    def test_own_workspaces_storage_is_returned(self):
        assert _reuse_on_controller('ws-a', 'st-a').storage_name == 'st-a'
        assert _reuse_on_controller(constants.SKYPILOT_DEFAULT_WORKSPACE,
                                    'st-d').storage_name == 'st-d'

    def test_another_workspaces_storage_reads_as_missing(self):
        with pytest.raises(exceptions.StorageSpecError,
                           match="'st-a' not found in workspace 'default'"):
            _reuse_on_controller(constants.SKYPILOT_DEFAULT_WORKSPACE, 'st-a')
        with pytest.raises(exceptions.StorageSpecError, match='not found'):
            _reuse_on_controller('ws-ro', 'st-a')


class TestCompletion:
    """Name completion is a listing and shows what `sky storage ls` would."""

    @pytest.mark.usefixtures('storages')
    def test_completion_shows_what_listing_would(self):
        assert _completed('bob') == _listed('bob')
        assert _completed('alice') == _listed('alice')
        assert _completed('admin') == _listed('admin')

    @pytest.mark.usefixtures('storages')
    def test_admin_sees_storage_of_a_removed_workspace(self):
        """Listing and completion both show an admin the storage of a
        workspace no longer in the config; others do not see it."""
        _add('st-gone', 'alice', 'ws-gone')
        assert 'st-gone' in _listed('admin')
        assert 'st-gone' in _completed('admin')
        assert 'st-gone' not in _completed('bob')

    @pytest.mark.usefixtures('storages')
    def test_without_an_authenticated_user_nothing_is_scoped(self):
        assert _completed(None) == ['st-a', 'st-d', 'st-ro']

    def test_workspace_added_after_boot_is_seen(self, storages):  # pylint: disable=redefined-outer-name
        """A sync handler does not get the executor's per-request config
        reload, so the route refreshes the workspace state itself."""
        config, service = storages
        assert _completed('bob') == ['st-d', 'st-ro']
        _add('st-new', 'alice', 'ws-new')
        config.write_text(
            json.dumps({'workspaces': dict(_WORKSPACES, **{'ws-new': {}})}))
        service.enforcer.add_policy('*', 'ws-new', '*')
        assert _completed('bob') == ['st-d', 'st-new', 'st-ro']
