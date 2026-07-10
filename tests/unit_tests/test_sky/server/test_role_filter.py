"""Unit tests for the role_filter body shim used to gate ambiguous endpoints
for the viewer role."""

from unittest import mock

import fastapi

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
