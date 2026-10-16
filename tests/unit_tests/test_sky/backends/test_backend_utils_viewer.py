"""Unit tests for the viewer-role defense-in-depth in backend_utils."""
# pylint: disable=unused-argument
# Pytest fixtures appear as unused arguments to pylint.

import os
from unittest import mock

import pytest

from sky.backends import backend_utils
from sky.skylet import constants
from sky.users import rbac


@pytest.fixture
def cleanup_user_id_env():
    yield
    if constants.USER_ID_ENV_VAR in os.environ:
        del os.environ[constants.USER_ID_ENV_VAR]


@mock.patch('sky.users.permission.permission_service')
def test_caller_is_viewer_returns_true(mock_svc, cleanup_user_id_env):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.VIEWER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    os.environ[constants.USER_ID_ENV_VAR] = 'viewer-bob'
    assert backend_utils._caller_is_viewer() is True
    enforcer.get_roles_for_user.assert_called_once_with('viewer-bob')


@mock.patch('sky.users.permission.permission_service')
def test_caller_is_viewer_returns_false_for_user(mock_svc, cleanup_user_id_env):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.USER.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    os.environ[constants.USER_ID_ENV_VAR] = 'user-alice'
    assert backend_utils._caller_is_viewer() is False


@mock.patch('sky.users.permission.permission_service')
def test_caller_is_viewer_returns_false_for_admin(mock_svc,
                                                  cleanup_user_id_env):
    enforcer = mock.Mock()
    enforcer.get_roles_for_user.return_value = [rbac.RoleName.ADMIN.value]
    mock_svc._ensure_enforcer.return_value = enforcer

    os.environ[constants.USER_ID_ENV_VAR] = 'admin-eve'
    assert backend_utils._caller_is_viewer() is False


def test_caller_is_viewer_returns_false_no_env(cleanup_user_id_env):
    # USER_ID_ENV_VAR is unset (we are NOT in an executor worker
    # context — e.g. CLI/SDK on a local laptop). Must return False
    # without crashing.
    if constants.USER_ID_ENV_VAR in os.environ:
        del os.environ[constants.USER_ID_ENV_VAR]
    assert backend_utils._caller_is_viewer() is False


@mock.patch('sky.users.permission.permission_service')
def test_caller_is_viewer_handles_init_failure(mock_svc, cleanup_user_id_env):
    # If the permission service is not initialized (e.g. the worker
    # process started before the API server set things up), we should
    # fail closed -> "not a viewer", so the check doesn't break
    # admin/user callers.
    mock_svc._ensure_enforcer.side_effect = RuntimeError('not ready')

    os.environ[constants.USER_ID_ENV_VAR] = 'someone'
    assert backend_utils._caller_is_viewer() is False
