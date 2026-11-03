"""Unit tests for the role_filter body shim used to gate ambiguous endpoints
for the viewer role."""

from unittest import mock

import fastapi
import pytest

from sky.server.requests import payloads
from sky.server.requests import role_filter
from sky.users import rbac
from sky.utils import common as common_lib


def _viewer_request():
    request = mock.Mock(spec=fastapi.Request)
    auth_user = mock.Mock()
    auth_user.id = 'viewer-bob'
    request.state.auth_user = auth_user
    return request


def _user_request():
    request = mock.Mock(spec=fastapi.Request)
    auth_user = mock.Mock()
    auth_user.id = 'user-alice'
    request.state.auth_user = auth_user
    return request


def _anonymous_request():
    request = mock.Mock(spec=fastapi.Request)
    request.state.auth_user = None
    return request


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_status_body_for_viewer(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    body = payloads.StatusBody(
        refresh=common_lib.StatusRefreshMode.FORCE,
        include_credentials=True,
    )
    out = role_filter.force_viewer_status_body(_viewer_request(), body)

    assert out.refresh == common_lib.StatusRefreshMode.NONE
    assert out.include_credentials is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_status_body_for_user_unchanged(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.USER.value]

    body = payloads.StatusBody(
        refresh=common_lib.StatusRefreshMode.FORCE,
        include_credentials=True,
    )
    out = role_filter.force_viewer_status_body(_user_request(), body)

    # Regular user — body must be unchanged.
    assert out.refresh == common_lib.StatusRefreshMode.FORCE
    assert out.include_credentials is True


def test_force_viewer_status_body_anonymous_unchanged():
    body = payloads.StatusBody(
        refresh=common_lib.StatusRefreshMode.FORCE,
        include_credentials=True,
    )
    out = role_filter.force_viewer_status_body(_anonymous_request(), body)
    # Anonymous (no auth_user) is treated like non-viewer.
    assert out.refresh == common_lib.StatusRefreshMode.FORCE
    assert out.include_credentials is True


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_queue_body(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    body = payloads.JobsQueueBody(refresh=True)
    out = role_filter.force_viewer_jobs_queue_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_queue_v2_body(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    body = payloads.JobsQueueV2Body(refresh=True)
    out = role_filter.force_viewer_jobs_queue_v2_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_logs_body(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    body = payloads.JobsLogsBody(refresh=True)
    out = role_filter.force_viewer_jobs_logs_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_download_logs_body(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    body = payloads.JobsDownloadLogsBody(name='job', job_id=1, refresh=True)
    out = role_filter.force_viewer_jobs_download_logs_body(
        _viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_volume_refresh(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.VIEWER.value]

    out = role_filter.force_viewer_volume_refresh(_viewer_request(),
                                                  refresh=True)
    assert out is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_volume_refresh_user_unchanged(mock_svc):
    mock_svc.roles_in_memory.return_value = [rbac.RoleName.USER.value]

    out = role_filter.force_viewer_volume_refresh(_user_request(), refresh=True)
    # Non-viewer is unaffected.
    assert out is True


def _admin_request():
    request = mock.Mock(spec=fastapi.Request)
    auth_user = mock.Mock()
    auth_user.id = 'admin-carol'
    request.state.auth_user = auth_user
    return request


def _roles_returning(mock_svc, roles):
    """Stub the resolved roles, which is what these filters ask for.

    Not the enforcer: `roles_in_memory` also resolves the server's own
    identities, so stubbing one layer down would let these tests pass while the
    filters disagreed with every other reader about such a principal.
    """
    mock_svc.roles_in_memory.return_value = roles


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_user_scopes_to_self(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.USER.value])
    assert role_filter.request_owner_scope(_user_request()) == 'user-alice'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_viewer_scopes_to_self(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.VIEWER.value])
    # A viewer is still a non-admin: reads are scoped to their own requests.
    assert role_filter.request_owner_scope(_viewer_request()) == 'viewer-bob'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_admin_unscoped(mock_svc):
    _roles_returning(mock_svc,
                     [rbac.RoleName.ADMIN.value, rbac.RoleName.USER.value])
    # Admin sees every user's requests.
    assert role_filter.request_owner_scope(_admin_request()) is None


def test_request_owner_scope_no_auth_unscoped():
    # No authentication configured -> ownership is unenforceable, unscoped.
    assert role_filter.request_owner_scope(_anonymous_request()) is None


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_user_forced(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.USER.value])
    # A non-admin cannot cancel on behalf of another user, even by asking.
    body = payloads.RequestCancelBody(request_ids=['abc'],
                                      user_id='someone-else')
    out = role_filter.force_caller_scope_cancel_body(_user_request(), body)
    assert out.user_id == 'user-alice'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_admin_preserved(mock_svc):
    _roles_returning(mock_svc,
                     [rbac.RoleName.ADMIN.value, rbac.RoleName.USER.value])
    # Admin keeps the client-supplied scope, incl. user_id=None (all users).
    body = payloads.RequestCancelBody(request_ids=None, user_id=None)
    out = role_filter.force_caller_scope_cancel_body(_admin_request(), body)
    assert out.user_id is None


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_admin_named_ids_unscoped(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.ADMIN.value])
    # Clients send their own user hash along with the ids. Kept as is, it hid
    # every other user's request from an admin's `sky api cancel <id>`.
    body = payloads.RequestCancelBody(request_ids=['abc'],
                                      user_id='admin-carol')
    out = role_filter.force_caller_scope_cancel_body(_admin_request(), body)
    assert out.request_ids == ['abc']
    assert out.user_id is None


def test_force_caller_scope_cancel_body_no_auth_named_ids_unscoped():
    # Without per-user auth there is no identity to scope by: the client
    # hash is not one, so a named request is cancelled whoever owns it.
    body = payloads.RequestCancelBody(request_ids=['abc'], user_id='abcdef12')
    out = role_filter.force_caller_scope_cancel_body(_anonymous_request(), body)
    assert out.user_id is None


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_admin_all_keeps_own_scope(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.ADMIN.value])
    # `sky api cancel --all` from an admin still cancels only their own.
    body = payloads.RequestCancelBody(request_ids=None, user_id='admin-carol')
    out = role_filter.force_caller_scope_cancel_body(_admin_request(), body)
    assert out.user_id == 'admin-carol'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_user_named_ids_forced(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.USER.value])
    # Even with the ids unscoped for admins, a non-admin naming another
    # user's request is still forced to their own scope.
    body = payloads.RequestCancelBody(request_ids=['abc'], user_id=None)
    out = role_filter.force_caller_scope_cancel_body(_user_request(), body)
    assert out.user_id == 'user-alice'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_viewer_forbidden(mock_svc):
    _roles_returning(mock_svc, [rbac.RoleName.VIEWER.value])
    body = payloads.RequestCancelBody(request_ids=['abc'])
    with pytest.raises(fastapi.HTTPException) as exc:
        role_filter.force_caller_scope_cancel_body(_viewer_request(), body)
    assert exc.value.status_code == 403
