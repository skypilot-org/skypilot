"""Unit tests for /users/batch_update and the admin demotion check."""

from unittest import mock

import fastapi
import pytest

from sky import models
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import server
from sky.utils import common
from sky.utils import resource_checker


@pytest.fixture
def admin_request():
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.auth_user = None  # Unauthenticated == admin in current logic
    return request


def _mk_user(user_id, name='User'):
    return models.User(id=user_id, name=name)


class TestUserBatchUpdate:
    """Tests for POST /users/batch_update."""

    @mock.patch('sky.users.server._user_lock')
    @mock.patch('sky.utils.resource_checker.check_user_role_demotion')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @mock.patch('sky.global_user_state.get_user')
    def test_promote_two_users_to_admin(self, mock_get_user,
                                        mock_get_users_for_role,
                                        mock_get_user_roles, mock_update_role,
                                        mock_demotion_check, mock_user_lock,
                                        admin_request):
        mock_user_lock.return_value = mock.MagicMock(__enter__=mock.MagicMock(),
                                                     __exit__=mock.MagicMock())
        mock_get_users_for_role.return_value = []
        mock_get_user_roles.return_value = ['user']
        mock_get_user.side_effect = lambda uid: _mk_user(uid,
                                                         name=f'name-{uid}')

        body = payloads.UserBatchUpdateBody(user_ids=['u1', 'u2'], role='admin')

        result = server.user_batch_update(admin_request, body)

        assert result == {'succeeded': ['u1', 'u2'], 'failed': []}
        assert mock_update_role.call_count == 2
        # Promotions never trigger the demotion check.
        mock_demotion_check.assert_not_called()

    @mock.patch('sky.users.server._user_lock')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @mock.patch('sky.global_user_state.get_user')
    def test_internal_user_and_missing_user_are_isolated_failures(
            self, mock_get_user, mock_get_users_for_role, mock_get_user_roles,
            mock_update_role, mock_user_lock, admin_request):
        mock_user_lock.return_value = mock.MagicMock(__enter__=mock.MagicMock(),
                                                     __exit__=mock.MagicMock())
        mock_get_users_for_role.return_value = []
        mock_get_user_roles.return_value = ['user']

        def fake_get_user(uid):
            if uid == 'good':
                return _mk_user(uid, name='good')
            if uid == constants.SKYPILOT_SYSTEM_USER_ID:
                return _mk_user(uid, name='system')
            if uid == constants.SKYPILOT_SYSTEM_VIEWER_USER_ID:
                return _mk_user(uid, name='system-viewer')
            if uid == common.SERVER_ID:
                return _mk_user(uid, name='server')
            return None

        mock_get_user.side_effect = fake_get_user

        body = payloads.UserBatchUpdateBody(user_ids=[
            'good',
            constants.SKYPILOT_SYSTEM_USER_ID,
            constants.SKYPILOT_SYSTEM_VIEWER_USER_ID,
            common.SERVER_ID,
            'unknown',
        ],
                                            role='admin')

        result = server.user_batch_update(admin_request, body)

        assert result['succeeded'] == ['good']
        failed_ids = sorted(f['user_id'] for f in result['failed'])
        assert failed_ids == sorted([
            constants.SKYPILOT_SYSTEM_USER_ID,
            constants.SKYPILOT_SYSTEM_VIEWER_USER_ID,
            common.SERVER_ID,
            'unknown',
        ])
        # update_role only called for the good user.
        mock_update_role.assert_called_once_with('good', 'admin')

    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    def test_non_admin_caller_is_forbidden(self, mock_get_user_roles):
        request = mock.MagicMock(spec=fastapi.Request)
        request.state = mock.MagicMock()
        request.state.auth_user = _mk_user('caller', name='caller')
        mock_get_user_roles.return_value = ['user']

        body = payloads.UserBatchUpdateBody(user_ids=['u1'], role='admin')

        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_batch_update(request, body)
        assert exc_info.value.status_code == 403

    def test_empty_user_ids_rejected(self, admin_request):
        body = payloads.UserBatchUpdateBody(user_ids=[], role='admin')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_batch_update(admin_request, body)
        assert exc_info.value.status_code == 400

    @mock.patch('sky.users.server._user_lock')
    @mock.patch('sky.utils.resource_checker.check_user_role_demotion')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @mock.patch('sky.global_user_state.get_user')
    def test_demotion_failure_isolated_to_offending_user(
            self, mock_get_user, mock_get_users_for_role, mock_get_user_roles,
            mock_update_role, mock_demotion_check, mock_user_lock,
            admin_request):
        mock_user_lock.return_value = mock.MagicMock(__enter__=mock.MagicMock(),
                                                     __exit__=mock.MagicMock())
        mock_get_users_for_role.return_value = ['u1', 'u2', 'u3']  # all admins
        mock_get_user_roles.return_value = ['admin']
        mock_get_user.side_effect = lambda uid: _mk_user(uid,
                                                         name=f'name-{uid}')

        def fake_demotion_check(uid, remaining_admin_user_ids=None):
            if uid == 'u2':
                raise ValueError(
                    "user 'u2' has active resources in 'private-ws'")

        mock_demotion_check.side_effect = fake_demotion_check

        body = payloads.UserBatchUpdateBody(user_ids=['u1', 'u2', 'u3'],
                                            role='user')

        result = server.user_batch_update(admin_request, body)

        assert sorted(result['succeeded']) == ['u1', 'u3']
        assert len(result['failed']) == 1
        assert result['failed'][0]['user_id'] == 'u2'
        # update_role only called for the two that passed the check.
        assert mock_update_role.call_count == 2

    @mock.patch('sky.users.server._user_lock')
    @mock.patch('sky.utils.resource_checker.check_user_role_demotion')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @mock.patch('sky.global_user_state.get_user')
    def test_demotion_check_also_fires_for_admin_to_viewer(
            self, mock_get_user, mock_get_users_for_role, mock_get_user_roles,
            mock_update_role, mock_demotion_check, mock_user_lock,
            admin_request):
        mock_user_lock.return_value = mock.MagicMock(__enter__=mock.MagicMock(),
                                                     __exit__=mock.MagicMock())
        mock_get_users_for_role.return_value = ['u1']  # u1 is the only admin
        mock_get_user_roles.return_value = ['admin']
        mock_get_user.return_value = _mk_user('u1', name='alice')

        body = payloads.UserBatchUpdateBody(user_ids=['u1'], role='viewer')
        result = server.user_batch_update(admin_request, body)

        assert result == {'succeeded': ['u1'], 'failed': []}
        mock_demotion_check.assert_called_once()
        # When demoting to viewer, the target is removed from the remaining
        # admin set (same as demoting to user).
        _, kwargs = mock_demotion_check.call_args
        assert kwargs['remaining_admin_user_ids'] == set()
        mock_update_role.assert_called_once_with('u1', 'viewer')

    @mock.patch('sky.users.server._user_lock')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @mock.patch('sky.global_user_state.get_user')
    def test_noop_when_role_unchanged(self, mock_get_user,
                                      mock_get_users_for_role,
                                      mock_get_user_roles, mock_update_role,
                                      mock_user_lock, admin_request):
        mock_user_lock.return_value = mock.MagicMock(__enter__=mock.MagicMock(),
                                                     __exit__=mock.MagicMock())
        mock_get_users_for_role.return_value = []
        mock_get_user_roles.return_value = ['admin']
        mock_get_user.return_value = _mk_user('u1', name='alice')

        body = payloads.UserBatchUpdateBody(user_ids=['u1'], role='admin')

        result = server.user_batch_update(admin_request, body)

        assert result == {'succeeded': ['u1'], 'failed': []}
        mock_update_role.assert_not_called()


class TestUserRoleDemotion:
    """Tests for resource_checker.check_user_role_demotion."""

    # check_user_role_demotion calls skypilot_config.safe_reload_config() at
    # the top to guarantee fresh workspace data for sync endpoint callers
    # (see resource_checker.py). Stub it out in unit tests so we don't hit
    # the real filesystem / file lock.
    @pytest.fixture(autouse=True)
    def _stub_safe_reload(self):
        with mock.patch('sky.skypilot_config.safe_reload_config'):
            yield

    @mock.patch('sky.utils.resource_checker._get_active_resources')
    @mock.patch('sky.skypilot_config.get_nested')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    def test_no_workspaces_is_a_noop(self, mock_get_users_for_role,
                                     mock_get_ws_users, mock_get_nested,
                                     mock_get_active):
        mock_get_nested.return_value = {}
        # Should not raise.
        resource_checker.check_user_role_demotion('u1')
        mock_get_active.assert_not_called()

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker._get_active_resources')
    @mock.patch('sky.skypilot_config.get_nested')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    def test_blocks_when_user_has_active_cluster_in_lost_workspace(
            self, mock_get_users_for_role, mock_get_ws_users, mock_get_nested,
            mock_get_active, mock_get_user):
        mock_get_nested.return_value = {
            'private-locked': {
                'private': True,
                'allowed_users': []
            },
        }
        mock_get_ws_users.return_value = []  # demoted user not allowed
        mock_get_users_for_role.return_value = []  # no other admins
        mock_get_active.return_value = ([{
            'name': 'c1',
            'user_hash': 'u1',
            'workspace': 'private-locked'
        }], [])
        mock_get_user.return_value = _mk_user('u1', name='alice')

        with pytest.raises(ValueError) as exc_info:
            resource_checker.check_user_role_demotion('u1')
        msg = str(exc_info.value)
        assert "'alice'" in msg
        assert 'private-locked' in msg
        assert 'c1' in msg

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker._get_active_resources')
    @mock.patch('sky.skypilot_config.get_nested')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    def test_allows_when_user_remains_in_allowed_users(
            self, mock_get_users_for_role, mock_get_ws_users, mock_get_nested,
            mock_get_active, mock_get_user):
        mock_get_nested.return_value = {
            'private-still-allowed': {
                'private': True,
                'allowed_users': ['u1']
            },
        }
        mock_get_ws_users.return_value = ['u1']  # demoted user is allowed
        mock_get_users_for_role.return_value = []
        mock_get_active.return_value = ([{
            'name': 'c1',
            'user_hash': 'u1',
            'workspace': 'private-still-allowed'
        }], [])
        mock_get_user.return_value = _mk_user('u1', name='alice')

        # Should not raise.
        resource_checker.check_user_role_demotion('u1')

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker._get_active_resources')
    @mock.patch('sky.skypilot_config.get_nested')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    def test_allows_when_user_only_has_resources_in_public_workspace(
            self, mock_get_users_for_role, mock_get_ws_users, mock_get_nested,
            mock_get_active, mock_get_user):
        mock_get_nested.return_value = {
            'public-ws': {
                'private': False
            },
        }
        mock_get_users_for_role.return_value = []
        mock_get_active.return_value = ([{
            'name': 'c1',
            'user_hash': 'u1',
            'workspace': 'public-ws'
        }], [])
        mock_get_user.return_value = _mk_user('u1', name='alice')

        # Should not raise (public workspace not even checked).
        resource_checker.check_user_role_demotion('u1')
        mock_get_ws_users.assert_not_called()

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker._get_active_resources')
    @mock.patch('sky.skypilot_config.get_nested')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    def test_remaining_admin_set_overrides_lookup(self, mock_get_users_for_role,
                                                  mock_get_ws_users,
                                                  mock_get_nested,
                                                  mock_get_active,
                                                  mock_get_user):
        """When two admins are demoted at once, only the post-batch admin
        set should be considered for access."""
        mock_get_nested.return_value = {
            'private-locked': {
                'private': True,
                'allowed_users': []
            },
        }
        mock_get_ws_users.return_value = []
        # Live policy says u1 and u2 are admins; pretend they're both being
        # demoted in this batch.
        mock_get_users_for_role.return_value = ['u1', 'u2']
        mock_get_active.return_value = ([{
            'name': 'c1',
            'user_hash': 'u1',
            'workspace': 'private-locked'
        }], [])
        mock_get_user.return_value = _mk_user('u1', name='alice')

        # Without the override, u2 would "save" u1. With the override
        # (remaining = empty set), u1 has nothing to fall back on.
        with pytest.raises(ValueError):
            resource_checker.check_user_role_demotion(
                'u1', remaining_admin_user_ids=set())

        # And if the caller passes a remaining admin set that does include
        # u1 (somehow), the check is satisfied.
        resource_checker.check_user_role_demotion(
            'u1', remaining_admin_user_ids={'u1'})
