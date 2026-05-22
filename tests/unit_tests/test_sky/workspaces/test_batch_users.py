"""Unit tests for workspace batch_add_users / batch_remove_users."""

from unittest import mock

import pytest

from sky import models
from sky.workspaces import core


def _mk_user(user_id, name):
    return models.User(id=user_id, name=name)


class TestBatchAddUsersToWorkspaces:

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_happy_path_adds_username(self, mock_get_all_users,
                                      mock_validate_config,
                                      mock_validate_changes, mock_update_config,
                                      mock_get_ws_users, mock_update_policy):
        captured = {}

        # Workspace starts empty; after the add, the post-update lookup
        # returns just the added user.
        mock_get_ws_users.side_effect = [
            [],  # initial resolved_current for p1
            ['u1'],  # post-update lookup for policy refresh
        ]

        def fake_update(modifier):
            workspaces = {'p1': {'private': True, 'allowed_users': []}}
            modifier(workspaces)
            captured['workspaces'] = workspaces

        mock_update_config.side_effect = fake_update

        result = core.batch_add_users_to_workspaces(['p1'], ['u1'])

        assert result == {'succeeded': ['p1'], 'failed': []}
        # Username preferred over user_id since 'alice' is unique.
        assert captured['workspaces']['p1']['allowed_users'] == ['alice']
        mock_validate_config.assert_called_once()
        # batch_add does not need the workspace-change validation: it only
        # adds users (no removed_users, no private toggle).
        mock_validate_changes.assert_not_called()

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch(
        'sky.global_user_state.get_all_users',
        return_value=[
            _mk_user('u1', 'alice'),
            _mk_user('u2', 'alice'),  # username collision
        ])
    def test_collision_falls_back_to_user_id(self, mock_get_all_users,
                                             mock_validate_config,
                                             mock_validate_changes,
                                             mock_update_config,
                                             mock_get_ws_users,
                                             mock_update_policy):
        captured = {}
        mock_get_ws_users.side_effect = [[], ['u1']]

        def fake_update(modifier):
            workspaces = {'p1': {'private': True, 'allowed_users': []}}
            modifier(workspaces)
            captured['workspaces'] = workspaces

        mock_update_config.side_effect = fake_update

        result = core.batch_add_users_to_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        # username 'alice' is ambiguous → must use user_id.
        assert captured['workspaces']['p1']['allowed_users'] == ['u1']

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=['u1'])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_public_workspace_is_rejected_per_workspace(
            self, mock_get_all_users, mock_validate_config,
            mock_validate_changes, mock_update_config, mock_get_ws_users,
            mock_update_policy):

        def fake_update(modifier):
            workspaces = {
                'p1': {
                    'private': True,
                    'allowed_users': []
                },
                'pub': {
                    'private': False
                },
            }
            modifier(workspaces)

        mock_update_config.side_effect = fake_update

        result = core.batch_add_users_to_workspaces(['p1', 'pub'], ['u1'])
        assert result['succeeded'] == ['p1']
        assert len(result['failed']) == 1
        assert result['failed'][0]['workspace_name'] == 'pub'
        assert 'not private' in result['failed'][0]['error']

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=['u1'])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_user_already_present_is_a_noop_success(self, mock_get_all_users,
                                                    mock_validate_config,
                                                    mock_validate_changes,
                                                    mock_update_config,
                                                    mock_get_ws_users,
                                                    mock_update_policy):

        def fake_update(modifier):
            workspaces = {
                'p1': {
                    'private': True,
                    'allowed_users': ['alice']
                },
            }
            modifier(workspaces)

        mock_update_config.side_effect = fake_update

        result = core.batch_add_users_to_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        # No validation calls because there was no change.
        mock_validate_config.assert_not_called()
        mock_validate_changes.assert_not_called()

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_unknown_user_id_is_recorded_as_failure(self, mock_get_all_users,
                                                    mock_update_config,
                                                    mock_update_policy):
        result = core.batch_add_users_to_workspaces(['p1'], ['unknown'])
        # No real workspaces touched; the unknown user surfaces as a failure.
        assert result['succeeded'] == []
        assert any(f['error'].startswith('User unknown does not exist')
                   for f in result['failed'])
        # Modifier never reached config-update path.
        mock_update_config.assert_not_called()

    def test_empty_args_rejected(self):
        with pytest.raises(ValueError):
            core.batch_add_users_to_workspaces([], ['u1'])
        with pytest.raises(ValueError):
            core.batch_add_users_to_workspaces(['p1'], [])


class TestBatchRemoveUsersFromWorkspaces:

    @staticmethod
    def _patch_initial_workspaces(workspaces):
        """Patch skypilot_config.to_dict to return the given workspaces.

        batch_remove_users_from_workspaces reads the pre-modification
        workspace config OUTSIDE the file lock (in the validation pre-pass),
        so tests need to stub that read in addition to the modifier flow.
        """
        return mock.patch('sky.skypilot_config.to_dict',
                          return_value={'workspaces': workspaces})

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=[])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_removes_username_form(self, mock_get_all_users,
                                   mock_validate_config, mock_validate_changes,
                                   mock_update_config, mock_get_ws_users,
                                   mock_update_policy):
        initial = {'p1': {'private': True, 'allowed_users': ['alice', 'bob']}}
        captured = {}

        def fake_update(modifier):
            workspaces = {k: dict(v) for k, v in initial.items()}
            modifier(workspaces)
            captured['workspaces'] = workspaces

        mock_update_config.side_effect = fake_update

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        assert captured['workspaces']['p1']['allowed_users'] == ['bob']

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=[])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_removes_user_id_form(self, mock_get_all_users,
                                  mock_validate_config, mock_validate_changes,
                                  mock_update_config, mock_get_ws_users,
                                  mock_update_policy):
        initial = {'p1': {'private': True, 'allowed_users': ['u1', 'bob']}}
        captured = {}

        def fake_update(modifier):
            workspaces = {k: dict(v) for k, v in initial.items()}
            modifier(workspaces)
            captured['workspaces'] = workspaces

        mock_update_config.side_effect = fake_update

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        assert captured['workspaces']['p1']['allowed_users'] == ['bob']

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=[])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock')
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_removes_both_forms_when_stale_dual_entry(self, mock_get_all_users,
                                                      mock_validate_config,
                                                      mock_validate_changes,
                                                      mock_update_config,
                                                      mock_get_ws_users,
                                                      mock_update_policy):
        # Workspace has the same user listed under BOTH forms (stale state).
        initial = {
            'p1': {
                'private': True,
                'allowed_users': ['alice', 'u1', 'bob']
            }
        }
        captured = {}

        def fake_update(modifier):
            workspaces = {k: dict(v) for k, v in initial.items()}
            modifier(workspaces)
            captured['workspaces'] = workspaces

        mock_update_config.side_effect = fake_update

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        # Both stale entries for u1 must be stripped in one call.
        assert captured['workspaces']['p1']['allowed_users'] == ['bob']

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_public_workspace_is_rejected(self, mock_get_all_users,
                                          mock_update_config,
                                          mock_update_policy):
        initial = {'pub': {'private': False}}

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['pub'], ['u1'])
        assert result['succeeded'] == []
        assert result['failed'][0]['workspace_name'] == 'pub'
        # No modifier should run because nothing validated.
        mock_update_config.assert_not_called()

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_user_not_in_allowed_is_noop_success(self, mock_get_all_users,
                                                 mock_update_config,
                                                 mock_update_policy):
        initial = {'p1': {'private': True, 'allowed_users': ['bob']}}

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['p1'], ['u1'])
        assert result == {'succeeded': ['p1'], 'failed': []}
        # No-op should not enter the modifier path.
        mock_update_config.assert_not_called()

    @mock.patch(
        'sky.users.permission.permission_service.update_workspace_policy')
    @mock.patch('sky.workspaces.utils.get_workspace_users', return_value=[])
    @mock.patch('sky.workspaces.core._update_workspaces_config')
    @mock.patch(
        'sky.workspaces.core._validate_workspace_config_changes_with_lock',
        side_effect=ValueError(
            "Cannot remove user 'alice' because the user has 1 active "
            'cluster(s)'))
    @mock.patch('sky.workspaces.core._validate_workspace_config')
    @mock.patch('sky.global_user_state.get_all_users',
                return_value=[_mk_user('u1', 'alice')])
    def test_active_resources_blocks_removal(self, mock_get_all_users,
                                             mock_validate_config,
                                             mock_validate_changes,
                                             mock_update_config,
                                             mock_get_ws_users,
                                             mock_update_policy):
        initial = {'p1': {'private': True, 'allowed_users': ['alice']}}

        with self._patch_initial_workspaces(initial):
            result = core.batch_remove_users_from_workspaces(['p1'], ['u1'])

        assert result['succeeded'] == []
        assert result['failed'][0]['workspace_name'] == 'p1'
        assert 'active cluster' in result['failed'][0]['error']
        # Validation failed in the pre-pass, so the modifier never runs.
        mock_update_config.assert_not_called()

    def test_empty_args_rejected(self):
        with pytest.raises(ValueError):
            core.batch_remove_users_from_workspaces([], ['u1'])
        with pytest.raises(ValueError):
            core.batch_remove_users_from_workspaces(['p1'], [])
