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
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    body = payloads.StatusBody(
        refresh=common_lib.StatusRefreshMode.FORCE,
        include_credentials=True,
    )
    out = role_filter.force_viewer_status_body(_viewer_request(), body)

    assert out.refresh == common_lib.StatusRefreshMode.NONE
    assert out.include_credentials is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_status_body_for_user_unchanged(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.USER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

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
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    body = payloads.JobsQueueBody(refresh=True)
    out = role_filter.force_viewer_jobs_queue_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_queue_v2_body(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    body = payloads.JobsQueueV2Body(refresh=True)
    out = role_filter.force_viewer_jobs_queue_v2_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_logs_body(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    body = payloads.JobsLogsBody(refresh=True)
    out = role_filter.force_viewer_jobs_logs_body(_viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_jobs_download_logs_body(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    body = payloads.JobsDownloadLogsBody(name='job', job_id=1, refresh=True)
    out = role_filter.force_viewer_jobs_download_logs_body(
        _viewer_request(), body)
    assert out.refresh is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_volume_refresh(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    out = role_filter.force_viewer_volume_refresh(_viewer_request(),
                                                  refresh=True)
    assert out is False


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_viewer_volume_refresh_user_unchanged(mock_svc):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.USER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    out = role_filter.force_viewer_volume_refresh(_user_request(), refresh=True)
    # Non-viewer is unaffected.
    assert out is True


def _admin_request():
    request = mock.Mock(spec=fastapi.Request)
    auth_user = mock.Mock()
    auth_user.id = 'admin-carol'
    request.state.auth_user = auth_user
    return request


def _enforcer_returning(mock_svc, roles):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = roles
    mock_svc._ensure_enforcer.return_value = enforcer


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_user_scopes_to_self(mock_svc):
    _enforcer_returning(mock_svc, [rbac.RoleName.USER.value])
    assert role_filter.request_owner_scope(_user_request()) == 'user-alice'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_viewer_scopes_to_self(mock_svc):
    _enforcer_returning(mock_svc, [rbac.RoleName.VIEWER.value])
    # A viewer is still a non-admin: reads are scoped to their own requests.
    assert role_filter.request_owner_scope(_viewer_request()) == 'viewer-bob'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_request_owner_scope_admin_unscoped(mock_svc):
    _enforcer_returning(mock_svc,
                        [rbac.RoleName.ADMIN.value, rbac.RoleName.USER.value])
    # Admin sees every user's requests.
    assert role_filter.request_owner_scope(_admin_request()) is None


def test_request_owner_scope_no_auth_unscoped():
    # No authentication configured -> ownership is unenforceable, unscoped.
    assert role_filter.request_owner_scope(_anonymous_request()) is None


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_user_forced(mock_svc):
    _enforcer_returning(mock_svc, [rbac.RoleName.USER.value])
    # A non-admin cannot cancel on behalf of another user, even by asking.
    body = payloads.RequestCancelBody(request_ids=['abc'],
                                      user_id='someone-else')
    out = role_filter.force_caller_scope_cancel_body(_user_request(), body)
    assert out.user_id == 'user-alice'


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_admin_preserved(mock_svc):
    _enforcer_returning(mock_svc,
                        [rbac.RoleName.ADMIN.value, rbac.RoleName.USER.value])
    # Admin keeps the client-supplied scope, incl. user_id=None (all users).
    body = payloads.RequestCancelBody(request_ids=None, user_id=None)
    out = role_filter.force_caller_scope_cancel_body(_admin_request(), body)
    assert out.user_id is None


@mock.patch.object(role_filter.permission, 'permission_service')
def test_force_caller_scope_cancel_body_viewer_forbidden(mock_svc):
    _enforcer_returning(mock_svc, [rbac.RoleName.VIEWER.value])
    body = payloads.RequestCancelBody(request_ids=['abc'])
    with pytest.raises(fastapi.HTTPException) as exc:
        role_filter.force_caller_scope_cancel_body(_viewer_request(), body)
    assert exc.value.status_code == 403
