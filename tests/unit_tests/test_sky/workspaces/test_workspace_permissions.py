"""Unit tests for workspace permissions."""

import inspect
import pathlib
import unittest
from unittest import mock

import sky
from sky import models
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core
from sky.workspaces import utils as workspaces_utils


class TestWorkspacePermissions(unittest.TestCase):
    """Test workspace permission functionality."""

    def setUp(self):
        """Set up test environment."""
        # Create mock users
        self.user1 = models.User(id='user1', name='Alice')
        self.user2 = models.User(id='user2', name='Bob')
        self.user3 = models.User(id='user3', name='Charlie')
        # Create users with duplicate names to test conflict resolution
        self.user4 = models.User(id='user4', name='Alice')  # Same name as user1
        self.user5 = models.User(id='user5', name=None)  # User with no name
        self.all_users = [
            self.user1, self.user2, self.user3, self.user4, self.user5
        ]

    @mock.patch('sky.global_user_state.get_all_users')
    def test_public_workspace_config(self, mock_get_users):
        """Test that public workspace config returns wildcard."""
        mock_get_users.return_value = self.all_users
        public_config = {'private': False}
        users = workspaces_utils.get_workspace_users(public_config)
        self.assertEqual(users, ['*'],
                         "Public workspace should return ['*'] for all users")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_config(self, mock_get_users):
        """Test that private workspace config returns specific users."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['Bob', 'Charlie']}
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user2', 'user3']  # user IDs for Bob and Charlie
        self.assertEqual(set(users), set(expected_users),
                         "Private workspace should return specific user IDs")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_with_unknown_user(self, mock_get_users):
        """Test private workspace with unknown user."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Bob', 'UnknownUser']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user2']  # Only Bob's user ID
        self.assertEqual(
            users, expected_users,
            "Unknown users should be ignored in private workspace")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_workspace_default_public(self, mock_get_users):
        """Test that workspace without 'private' key defaults to public."""
        mock_get_users.return_value = self.all_users
        default_config = {}
        users = workspaces_utils.get_workspace_users(default_config)
        self.assertEqual(
            users, ['*'],
            "Workspace without 'private' key should default to public")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_with_user_ids(self, mock_get_users):
        """Test private workspace using user IDs directly."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['user1', 'user2']}
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1', 'user2']
        self.assertEqual(set(users), set(expected_users),
                         "Private workspace should accept user IDs directly")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_mixed_names_and_ids(self, mock_get_users):
        """Test private workspace with mix of user names and IDs."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['user1', 'Bob', 'user3']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user1', 'user2', 'user3']
        self.assertEqual(
            set(users), set(expected_users),
            "Private workspace should handle mix of names and IDs")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_duplicate_user_names_raises_error(
            self, mock_get_users):
        """Test private workspace with duplicate user names raises ValueError."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': ['Alice']}

        with self.assertRaises(ValueError) as context:
            workspaces_utils.get_workspace_users(private_config)

        self.assertIn('User \'Alice\' has multiple IDs', str(context.exception))
        self.assertIn('user1, user4', str(context.exception))
        self.assertIn('Please specify the user ID instead',
                      str(context.exception))

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_empty_allowed_users(self, mock_get_users):
        """Test private workspace with empty allowed_users list."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True, 'allowed_users': []}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace with empty allowed_users should return empty list"
        )

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_no_allowed_users_key(self, mock_get_users):
        """Test private workspace without allowed_users key."""
        mock_get_users.return_value = self.all_users
        private_config = {'private': True}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace without allowed_users should return empty list")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_only_unknown_users(self, mock_get_users):
        """Test private workspace with only unknown users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['UnknownUser1', 'UnknownUser2']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [],
            "Private workspace with only unknown users should return empty list"
        )

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_user_with_no_name(self, mock_get_users):
        """Test private workspace with user that has no name."""
        mock_get_users.return_value = self.all_users
        # Try to use the ID of a user with no name
        private_config = {'private': True, 'allowed_users': ['user5']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, ['user5'],
            "Private workspace should accept user ID even if user has no name")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_unique_user_name(self, mock_get_users):
        """Test private workspace with unique user name (no duplicates)."""
        mock_get_users.return_value = [self.user2,
                                       self.user3]  # Only Bob and Charlie
        private_config = {'private': True, 'allowed_users': ['Bob']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, ['user2'],
            "Private workspace should resolve unique user name to ID")

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.resolver.logger')
    def test_private_workspace_logs_warning_for_unknown_user(
            self, mock_logger, mock_get_users):
        """Test that warning is logged for unknown users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Bob', 'UnknownUser']
        }

        users = workspaces_utils.get_workspace_users(private_config)

        # Should return Bob's ID
        self.assertEqual(users, ['user2'])

        # Should log warning for unknown user
        mock_logger.warning.assert_called_once_with(
            "allowed_users entry 'UnknownUser' does not match any existing "
            'user record; skipping for now. Access will be granted '
            'automatically once this user logs in.')

    @mock.patch('sky.global_user_state.get_all_users')
    def test_private_workspace_preserves_order(self, mock_get_users):
        """Test that private workspace preserves order of allowed users."""
        mock_get_users.return_value = self.all_users
        private_config = {
            'private': True,
            'allowed_users': ['Charlie', 'Bob', 'user1']
        }
        users = workspaces_utils.get_workspace_users(private_config)
        expected_users = ['user3', 'user2', 'user1']  # Charlie, Bob, user1
        self.assertEqual(
            users, expected_users,
            "Private workspace should preserve order of allowed users")

    @mock.patch('sky.global_user_state.get_all_users')
    def test_empty_all_users_list(self, mock_get_users):
        """Test behavior when get_all_users returns empty list."""
        mock_get_users.return_value = []
        private_config = {'private': True, 'allowed_users': ['Alice', 'Bob']}
        users = workspaces_utils.get_workspace_users(private_config)
        self.assertEqual(
            users, [], "Should return empty list when no users exist in system")


class TestReadOnlyForNonMembers(unittest.TestCase):
    """Test the read_access read-only predicate."""

    def test_private_read_only(self):
        cfg = {'private': True, 'read_access': 'all'}
        self.assertTrue(workspaces_utils.is_read_only_for_non_members(cfg))

    @mock.patch('sky.skypilot_config.get_nested')
    def test_private_none_default(self, mock_get_nested):
        # With no org-wide default set (workspace_config.read_access), a private
        # workspace without its own read_access is members-only/hidden (not
        # read-only-visible). Mock get_nested so the test is hermetic and
        # doesn't read the ambient ~/.sky/config.yaml.
        mock_get_nested.return_value = 'allowed_users'
        self.assertFalse(
            workspaces_utils.is_read_only_for_non_members({'private': True}))
        self.assertFalse(
            workspaces_utils.is_read_only_for_non_members({
                'private': True,
                'read_access': 'allowed_users'
            }))

    def test_public_read_only_is_moot(self):
        # An open workspace is usable by everyone; the flag doesn't apply.
        cfg = {'private': False, 'read_access': 'all'}
        self.assertFalse(workspaces_utils.is_read_only_for_non_members(cfg))

    def test_empty_config(self):
        self.assertFalse(workspaces_utils.is_read_only_for_non_members({}))

    @mock.patch('sky.skypilot_config.get_nested')
    def test_global_default_applies_when_unset(self, mock_get_nested):
        # With the org-wide default set to `all`, a private workspace that
        # doesn't set its own read_access inherits it.
        mock_get_nested.return_value = 'all'
        self.assertTrue(
            workspaces_utils.is_read_only_for_non_members({'private': True}))
        # The org-wide default is read from workspace_config.read_access.
        self.assertEqual(mock_get_nested.call_args.args[0],
                         ('workspace_config', 'read_access'))
        # A per-workspace value overrides the global default.
        self.assertFalse(
            workspaces_utils.is_read_only_for_non_members({
                'private': True,
                'read_access': 'allowed_users'
            }))
        # The global default is moot for an open (non-private) workspace.
        self.assertFalse(
            workspaces_utils.is_read_only_for_non_members({'private': False}))


class TestReadOnlyWorkspaceQueries(unittest.TestCase):
    """get_read_only_workspace_names / is_read_only_workspace read live config.

    These live in workspaces.utils (moved from users.rbac) and drive the
    permission service's live read-only evaluation.
    """

    @staticmethod
    def _config(workspaces, global_default):

        def _get_nested(keys, default_value=None):
            if keys == ('workspaces',):
                return workspaces
            if keys == ('workspace_config', 'read_access'):
                return global_default
            return default_value

        return _get_nested

    @mock.patch('sky.skypilot_config.get_nested')
    def test_per_workspace_override(self, mock_get_nested):
        # Global default 'allowed_users': only the workspace with its own
        # read_access: all is read-only-visible.
        mock_get_nested.side_effect = self._config(
            {
                'w-ro': {
                    'private': True,
                    'read_access': 'all'
                },
                'w-priv': {
                    'private': True
                },
                'w-pub': {
                    'private': False
                },
            }, 'allowed_users')
        self.assertEqual(workspaces_utils.get_read_only_workspace_names(),
                         {'w-ro'})
        self.assertTrue(workspaces_utils.is_read_only_workspace('w-ro'))
        self.assertFalse(workspaces_utils.is_read_only_workspace('w-priv'))
        self.assertFalse(workspaces_utils.is_read_only_workspace('w-pub'))
        self.assertFalse(workspaces_utils.is_read_only_workspace('nonexistent'))

    @mock.patch('sky.skypilot_config.get_nested')
    def test_global_default_read_only(self, mock_get_nested):
        # Global default 'all': a private workspace with no override
        # inherits it; a per-workspace 'allowed_users' opts back out.
        mock_get_nested.side_effect = self._config(
            {
                'w-priv': {
                    'private': True
                },
                'w-none': {
                    'private': True,
                    'read_access': 'allowed_users'
                },
            }, 'all')
        self.assertEqual(workspaces_utils.get_read_only_workspace_names(),
                         {'w-priv'})
        self.assertTrue(workspaces_utils.is_read_only_workspace('w-priv'))
        self.assertFalse(workspaces_utils.is_read_only_workspace('w-none'))


class TestWorkspacesForUserReadOnlyFlag(unittest.TestCase):
    """workspaces_for_user annotates each workspace with a `read_only` flag.

    The flag is server-computed via is_read_only_for_non_members, so it applies
    the org-wide workspace_config.read_access fallback -- the dashboard
    must not have to re-derive it from the raw per-workspace field (which was
    the bug: a private workspace with no override but a global read-only default
    showed no badge).
    """

    @staticmethod
    def _config(workspaces, global_default):

        def _get_nested(keys, default_value=None):
            if keys == ('workspaces',):
                return dict(workspaces)
            if keys == ('workspace_config', 'read_access'):
                return global_default
            return default_value

        return _get_nested

    def _run(self, workspaces, global_default, accessible, writable):
        """Invoke workspaces_for_user with permissions/config mocked."""

        def _access_sets(user_id, names):
            # workspaces_for_user computes the read (visible) and write sets in
            # a single permission-service call.
            return set(accessible), set(writable)

        with mock.patch('sky.skypilot_config.get_nested',
                        side_effect=self._config(workspaces, global_default)), \
             mock.patch.object(workspaces_core.common_utils,
                               'get_current_user',
                               return_value=models.User(id='u', name='u')), \
             mock.patch.object(
                 workspaces_core.permission.permission_service,
                 'get_workspace_access_sets',
                 side_effect=_access_sets):
            return workspaces_core.workspaces_for_user('u')

    def test_global_default_read_only_sets_flag_without_override(self):
        # 'w-priv' is private with no per-workspace override; the org-wide
        # default read-only must make read_only True (the regression case).
        result = self._run(
            workspaces={
                'w-priv': {
                    'private': True
                },
                'w-none': {
                    'private': True,
                    'read_access': 'allowed_users'
                },
                'w-pub': {
                    'private': False
                },
            },
            global_default='all',
            accessible={'w-priv', 'w-none', 'w-pub'},
            writable={'w-pub'},
        )
        self.assertTrue(result['w-priv']['read_only'])
        # Per-workspace 'allowed_users' opts back out even under an 'all' default.
        self.assertFalse(result['w-none']['read_only'])
        # Public workspace: read-only is moot.
        self.assertFalse(result['w-pub']['read_only'])
        # The existing writable flag is unaffected.
        self.assertFalse(result['w-priv']['writable'])
        self.assertTrue(result['w-pub']['writable'])

    def test_global_default_none_only_flags_overrides(self):
        result = self._run(
            workspaces={
                'w-ro': {
                    'private': True,
                    'read_access': 'all'
                },
                'w-priv': {
                    'private': True
                },
            },
            global_default='allowed_users',
            accessible={'w-ro', 'w-priv'},
            writable=set(),
        )
        self.assertTrue(result['w-ro']['read_only'])
        self.assertFalse(result['w-priv']['read_only'])

    def test_non_writable_config_is_stripped_of_sensitive_fields(self):
        # A read-only-visible workspace the caller is NOT a member of must not
        # leak its member roster (allowed_users) or infra settings (gcp, etc.)
        # through /workspaces. Only the badges are exposed.
        result = self._run(
            workspaces={
                'w-priv': {
                    'private': True,
                    'allowed_users': ['alice@example.com', 'bob@example.com'],
                    'gcp': {
                        'project_id': 'secret-project'
                    },
                },
                'w-mine': {
                    'private': True,
                    'allowed_users': ['carol@example.com'],
                    'gcp': {
                        'project_id': 'my-project'
                    },
                },
            },
            global_default='all',
            accessible={'w-priv', 'w-mine'},
            writable={'w-mine'},
        )
        # Non-member workspace: stripped to badges only.
        self.assertEqual(set(result['w-priv'].keys()),
                         {'private', 'writable', 'read_only'})
        self.assertNotIn('allowed_users', result['w-priv'])
        self.assertNotIn('gcp', result['w-priv'])
        self.assertFalse(result['w-priv']['writable'])
        self.assertTrue(result['w-priv']['read_only'])
        self.assertTrue(result['w-priv']['private'])
        # Member workspace: full config retained.
        self.assertEqual(result['w-mine']['allowed_users'],
                         ['carol@example.com'])
        self.assertEqual(result['w-mine']['gcp'], {'project_id': 'my-project'})
        self.assertTrue(result['w-mine']['writable'])


class TestAccessibleDefaultsToWritable(unittest.TestCase):
    """"Accessible" must keep meaning "where can I act".

    Before read-only visibility existed, `get_accessible_workspace_names()`
    returned the member/open set, and every consumer treats its result as a set
    of usable choices (create-here dropdowns, mutation targets). Defaulting it
    to the read set silently folded read-only-visible workspaces into all of
    them, so a dropdown would offer a workspace whose create then fails. The
    read set is opt-in per call site.
    """

    def test_public_helper_defaults_to_write(self):
        with mock.patch.object(workspaces_core, '_load_workspaces',
                               return_value={'w': {}}), \
             mock.patch.object(workspaces_core,
                               '_accessible_workspace_names_for_user') as m, \
             mock.patch.object(workspaces_core.common_utils,
                               'get_current_user',
                               return_value=models.User(id='u', name='u')):
            workspaces_core.get_accessible_workspace_names()
        self.assertEqual(m.call_args.kwargs['action'],
                         workspace_constants.WORKSPACE_ACTION_WRITE)

    def test_read_is_opt_in(self):
        with mock.patch.object(workspaces_core, '_load_workspaces',
                               return_value={'w': {}}), \
             mock.patch.object(workspaces_core,
                               '_accessible_workspace_names_for_user') as m, \
             mock.patch.object(workspaces_core.common_utils,
                               'get_current_user',
                               return_value=models.User(id='u', name='u')):
            workspaces_core.get_accessible_workspace_names(
                action=workspace_constants.WORKSPACE_ACTION_READ)
        self.assertEqual(m.call_args.kwargs['action'],
                         workspace_constants.WORKSPACE_ACTION_READ)

    def test_private_helper_requires_an_explicit_action(self):
        """No default at all one level down, so nothing can inherit the wrong
        set by omission."""
        sig = inspect.signature(
            workspaces_core._accessible_workspace_names_for_user)  # pylint: disable=protected-access
        self.assertIs(sig.parameters['action'].default, inspect.Parameter.empty)

    def test_visibility_call_sites_ask_for_read(self):
        """The resource listings must keep asking for the READ set.

        These are the call sites the feature exists for: a non-member of a
        read-only workspace has to SEE its clusters/jobs and the workspace
        itself. They are the only places that deliberately depart from the
        writable default, so a well-meaning "simplify to the default" would
        silently hide those resources again. Everywhere else may rely on the
        default, which fails in the safe direction.
        """
        root = pathlib.Path(sky.__file__).parent
        missing = []
        for relative in [
                'backends/backend_utils.py',  # cluster listing filter
                'jobs/server/core.py',  # managed-job listing filter
                'server/server.py',  # enabled_clouds_batch API filter
        ]:
            if 'WORKSPACE_ACTION_READ' not in (root / relative).read_text():
                missing.append(relative)
        self.assertEqual(
            missing, [], f'{missing} no longer request the read set from '
            'get_accessible_workspace_names(). Listings must stay READ so '
            'read-only-visible workspaces keep showing their resources.')


if __name__ == '__main__':
    unittest.main()
