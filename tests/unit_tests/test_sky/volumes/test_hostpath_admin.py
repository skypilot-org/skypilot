"""k8s-hostpath volumes expose a node directory, so only an admin manages them.

Creating and deleting one is admin-only, and deleting can keep its files.
"""
import contextlib
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import provision
from sky.server import constants as server_constants
from sky.server import versions
from sky.server.requests import executor
from sky.users import permission
from sky.utils import status_lib
from sky.utils import volume as volume_utils
from sky.volumes.client import sdk as volumes_sdk
from sky.volumes.server import core
from sky.volumes.server import server
from sky.workspaces import core as workspaces_core

_ALICE = models.User(id='alice-id', name='alice')
_ADMIN = models.User(id='admin-id', name='admin')
_HOSTPATH = volume_utils.VolumeType.HOSTPATH.value
_PVC = volume_utils.VolumeType.PVC.value


def _config(name='vol', volume_type=_HOSTPATH, cleanup=True):
    config = {'host_path': '/mnt/data', 'cleanup_on_deletion': cleanup}
    if volume_type == _PVC:
        config = {'access_mode': 'ReadWriteMany'}
    return models.VolumeConfig(_version=1,
                               name=name,
                               type=volume_type,
                               cloud='kubernetes',
                               region='ctx1',
                               zone=None,
                               size=None,
                               config=config,
                               name_on_cloud=f'{name}-abc')


def _record(name='vol', volume_type=_HOSTPATH, user_hash='admin-id', **kw):
    return {
        'name': name,
        'workspace': 'default',
        'user_hash': user_hash,
        'status': status_lib.VolumeStatus.READY,
        'error_message': None,
        'handle': _config(name, volume_type, **kw),
    }


@pytest.fixture
def admins(monkeypatch):
    """Only `admin-id` holds the admin role."""
    monkeypatch.setattr(
        permission.permission_service, 'get_user_roles',
        lambda user_id: ['admin'] if user_id == 'admin-id' else ['user'])


@contextlib.contextmanager
def _request_state(auth_user):
    with mock.patch.object(fastapi.Request, 'state') as state:
        state.request_id = 'request-id'
        state.auth_user = auth_user
        yield


def _client():
    app = fastapi.FastAPI()
    app.include_router(server.router, prefix='/volumes')
    return TestClient(app)


# --- create -----------------------------------------------------------------


@pytest.mark.parametrize('volume_type, auth_user, status', [
    (_HOSTPATH, _ALICE, 400),
    (_HOSTPATH, _ADMIN, 200),
    (_HOSTPATH, None, 200),
    (_PVC, _ALICE, 200),
])
def test_only_an_admin_registers_a_hostpath_volume(monkeypatch, admins,
                                                   volume_type, auth_user,
                                                   status):
    schedule = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', schedule)
    config = ({'host_path': '/mnt/data'} if volume_type == _HOSTPATH else {})
    with _request_state(auth_user):
        response = _client().post('/volumes/apply',
                                  json={
                                      'name': 'vol',
                                      'volume_type': volume_type,
                                      'cloud': 'kubernetes',
                                      'region': 'ctx1',
                                      'size': '1Gi',
                                      'config': config,
                                  })
    assert response.status_code == status, response.text
    assert schedule.called == (status == 200)
    if status == 400:
        # A serialized error, which the SDK raises (it ignores a 403 here).
        assert 'PermissionDeniedError' in response.text


def test_validate_refuses_a_hostpath_volume_for_a_non_admin(admins):
    with _request_state(_ALICE):
        response = _client().post('/volumes/validate',
                                  json={
                                      'name': 'vol',
                                      'volume_type': _HOSTPATH,
                                      'infra': 'k8s/ctx1',
                                      'config': {
                                          'host_path': '/mnt/data'
                                      },
                                  })
    assert response.status_code == 400
    assert 'PermissionDeniedError' in response.text
    assert 'only be registered by an admin' in response.text


# --- delete -----------------------------------------------------------------


@pytest.mark.parametrize('names, auth_user, status', [
    (['hp'], _ALICE, 403),
    (['pvc', 'hp'], _ALICE, 403),
    (['pvc'], _ALICE, 200),
    (['hp'], _ADMIN, 200),
    (['hp'], None, 200),
])
def test_only_an_admin_deletes_a_hostpath_volume(monkeypatch, admins, names,
                                                 auth_user, status):
    schedule = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', schedule)
    monkeypatch.setattr(workspaces_core, 'check_volume_write_permission',
                        lambda user, name: None)
    records = {'hp': _record('hp'), 'pvc': _record('pvc', _PVC)}
    monkeypatch.setattr(global_user_state, 'get_volume_by_name', records.get)
    with _request_state(auth_user):
        response = _client().post('/volumes/delete', json={'names': names})
    assert response.status_code == status
    # A refused request deletes nothing, not even the volumes it may delete.
    assert schedule.called == (status == 200)


@pytest.fixture
def deleted_with(monkeypatch):
    """Runs core.volume_delete; returns the config the cloud was handed."""

    def run(record, fail=False, **kwargs):
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            lambda name: record)
        monkeypatch.setattr(global_user_state, 'delete_volume',
                            mock.MagicMock())
        monkeypatch.setattr(provision, 'get_volume_usedby',
                            lambda cloud, config: ([], []))
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock.MagicMock())
        delete = mock.MagicMock(
            side_effect=RuntimeError('cloud down') if fail else None)
        monkeypatch.setattr(provision, 'delete_volume', delete)
        core.volume_delete(['vol'], **kwargs)
        return delete.call_args[0][1]

    return run


@pytest.mark.parametrize('cleanup', [True, False])
def test_delete_follows_the_stored_cleanup_by_default(deleted_with, cleanup):
    config = deleted_with(_record(cleanup=cleanup))
    assert config.config['cleanup_on_deletion'] is cleanup


def test_no_cleanup_keeps_the_files(deleted_with):
    record = _record()
    config = deleted_with(record, no_cleanup=True)
    assert config.config['cleanup_on_deletion'] is False
    assert config.config['host_path'] == '/mnt/data'
    # The stored record is not changed.
    assert record['handle'].config['cleanup_on_deletion'] is True


def test_purge_drops_the_record_and_honors_no_cleanup(deleted_with):
    config = deleted_with(_record(), fail=True, purge=True, no_cleanup=True)
    assert config.config['cleanup_on_deletion'] is False
    global_user_state.delete_volume.assert_called_once_with('vol')  # pylint: disable=no-member


@pytest.mark.parametrize('remote_version, raises', [
    (server_constants.MIN_VOLUME_NO_CLEANUP_API_VERSION - 1, True),
    (None, True),
    (server_constants.MIN_VOLUME_NO_CLEANUP_API_VERSION, False),
])
def test_sdk_refuses_no_cleanup_on_a_server_that_would_wipe(
        monkeypatch, remote_version, raises):
    """An older server ignores the field and would wipe the host path."""
    monkeypatch.setattr(versions, 'get_remote_api_version',
                        lambda: remote_version)
    request = mock.MagicMock()
    monkeypatch.setattr(volumes_sdk.server_common, 'make_authenticated_request',
                        request)
    monkeypatch.setattr(volumes_sdk.server_common, 'get_request_id',
                        lambda response: 'rid')
    # Bypass the @check_server_healthy_or_start decorator's network call.
    monkeypatch.setattr('sky.server.common.check_server_healthy_or_start_fn',
                        lambda *a, **kw: None)
    if raises:
        with pytest.raises(exceptions.APINotSupportedError):
            volumes_sdk.delete(['vol'], no_cleanup=True)
        request.assert_not_called()
    else:
        volumes_sdk.delete(['vol'], no_cleanup=True)
        assert request.call_args[1]['json']['no_cleanup'] is True


# --- admin check ------------------------------------------------------------


def test_a_failed_role_lookup_is_not_admin(monkeypatch):
    monkeypatch.setattr(permission.permission_service, 'get_user_roles',
                        mock.MagicMock(side_effect=RuntimeError('db down')))
    assert not permission.is_admin('admin-id')


@pytest.fixture
def non_admin_on_server(monkeypatch):
    monkeypatch.setenv('IS_SKYPILOT_SERVER', '1')
    monkeypatch.delenv('IS_SKYPILOT_JOB_CONTROLLER', raising=False)
    monkeypatch.delenv('IS_SKYPILOT_SERVE_CONTROLLER', raising=False)
    monkeypatch.setattr(core.common_utils, 'get_current_user', lambda: _ALICE)
    monkeypatch.setattr(permission.permission_service, 'get_user_roles',
                        lambda user_id: ['user'])


def test_core_delete_refuses_a_non_admin_too(deleted_with, non_admin_on_server):
    """The record deleted is the one checked, whatever /volumes/delete saw."""
    with pytest.raises(exceptions.PermissionDeniedError):
        deleted_with(_record())
    provision.delete_volume.assert_not_called()  # pylint: disable=no-member


def test_core_apply_refuses_a_non_admin_too(non_admin_on_server):
    with pytest.raises(exceptions.PermissionDeniedError):
        core.volume_apply('vol', _HOSTPATH, 'kubernetes', 'ctx1', None, None,
                          {'host_path': '/mnt/data'})
