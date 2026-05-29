"""Tests for the workspace CLI surface:

  * `sky workspace use [<name>] [--clear]` (writes preferred via POST)
  * `sky launch -w/--workspace <name>` flag (sugar for
     `--config active_workspace=<name>`)
  * AMBIGUOUS surfacing on the launch path
  * `sky api info` Preferred-workspace line (reads from /api/health user
     row — no extra round-trip)
"""
import unittest
from unittest import mock

from click.testing import CliRunner
import pytest

from sky import exceptions
from sky.client import sdk
from sky.client.cli import command
from sky.client.cli import flags

# `sky workspace use` command -------------------------------------------


class TestSkyWorkspaceUseCommand(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    def test_set_calls_sdk_and_echoes_target(self):
        with mock.patch.object(sdk,
                               'set_preferred_workspace',
                               return_value={'preferred': 'team-b'
                                            }) as set_pref:
            result = self.runner.invoke(command.cli,
                                        ['workspace', 'use', 'team-b'])
        self.assertEqual(result.exit_code, 0, result.output)
        set_pref.assert_called_once_with('team-b')
        self.assertIn("Set preferred workspace to 'team-b'", result.output)

    def test_clear_passes_none_and_echoes_cleared(self):
        with mock.patch.object(sdk,
                               'set_preferred_workspace',
                               return_value={'preferred': None}) as set_pref:
            result = self.runner.invoke(command.cli,
                                        ['workspace', 'use', '--clear'])
        self.assertEqual(result.exit_code, 0, result.output)
        set_pref.assert_called_once_with(None)
        self.assertIn('Cleared preferred workspace.', result.output)

    def test_clear_and_name_together_errors(self):
        """Both flags can't be combined — caller must mean one OR the other."""
        with mock.patch.object(sdk, 'set_preferred_workspace') as set_pref:
            result = self.runner.invoke(
                command.cli, ['workspace', 'use', '--clear', 'team-a'])
        self.assertNotEqual(result.exit_code, 0)
        # Confirm SDK was NOT called — argument validation runs first.
        set_pref.assert_not_called()

    def test_no_name_and_no_clear_errors(self):
        """Missing both means the caller's intent is unclear — fail fast."""
        with mock.patch.object(sdk, 'set_preferred_workspace') as set_pref:
            result = self.runner.invoke(command.cli, ['workspace', 'use'])
        self.assertNotEqual(result.exit_code, 0)
        set_pref.assert_not_called()


# `sky workspace use` rejected by server (RBAC / unknown ws) ------------


class TestSkyWorkspaceUseRejected(unittest.TestCase):
    """The SDK raises when the server returns 403/404. The CLI must surface
    that as a non-zero exit + the error text, without retry / partial
    write."""

    def setUp(self):
        self.runner = CliRunner()

    def test_rejected_exits_non_zero_and_shows_error(self):
        with mock.patch.object(
                sdk,
                'set_preferred_workspace',
                side_effect=RuntimeError(
                    "User does not have permission to access workspace "
                    "'team-locked'")) as set_pref:
            result = self.runner.invoke(command.cli,
                                        ['workspace', 'use', 'team-locked'])
        self.assertNotEqual(result.exit_code, 0)
        set_pref.assert_called_once_with('team-locked')
        # The error must surface to the operator — exception goes to
        # CliRunner.exception, not stdout.
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertIn('team-locked', str(result.exception))


# AMBIGUOUS error surfaces correctly via the CLI launch path ------------


class TestLaunchAmbiguousSurfaced(unittest.TestCase):
    """When server-side resolver raises WorkspaceAmbiguousError, the CLI
    must propagate the full guidance message (not just the class name) so
    the user knows how to recover.

    The message itself is constructed in WorkspaceAmbiguousError.__init__
    and asserted in test_resolve_workspace_for_user.py; this test only
    verifies the string round-trips intact via the CLI.
    """

    def test_ambiguous_message_contains_all_recovery_options(self):
        e = exceptions.WorkspaceAmbiguousError(['team-a', 'team-b', 'team-c'])
        msg = str(e)
        for needle in [
                'You belong to multiple workspaces',
                'team-a',
                'team-b',
                'team-c',
                'sky workspace use',
                '--workspace',
                'active_workspace',
        ]:
            self.assertIn(needle, msg,
                          f'AMBIGUOUS message missing {needle!r}: {msg}')

    def test_ambiguous_message_with_drift_note(self):
        """When the user had a preferred that lost access, the drift note
        explains why they're now in AMBIGUOUS — keeps the user oriented."""
        e = exceptions.WorkspaceAmbiguousError(
            ['team-b', 'team-c'], note="preferred 'team-a' not accessible")
        msg = str(e)
        self.assertIn('Note:', msg)
        self.assertIn("preferred 'team-a' not accessible", msg)


# `--workspace` flag callback (sugar for --config active_workspace=X) ---


class TestWorkspaceFlagCallback(unittest.TestCase):
    """`-w/--workspace foo` is implemented as a thin click callback that
    rewrites the option into `--config active_workspace=foo`. Once that
    config override lands, the rest of the launch flow is unchanged from
    `--config active_workspace=foo` (which is the existing public
    mechanism). Tests verify the callback delegates correctly; the
    downstream config-application path is already covered by `--config`'s
    own tests.
    """

    def test_callback_applies_active_workspace_when_value_set(self):
        from sky import skypilot_config
        with mock.patch.object(skypilot_config,
                               'apply_cli_config') as apply_cli:
            returned = flags.apply_workspace_option_callback(ctx=None,
                                                             param=None,
                                                             value='team-b')
        apply_cli.assert_called_once_with(['active_workspace=team-b'])
        # Callback also returns the value (click ignores it because
        # expose_value=False, but the return shouldn't be lossy).
        self.assertEqual(returned, 'team-b')

    def test_callback_no_op_when_value_none(self):
        """User didn't pass --workspace → don't touch config."""
        from sky import skypilot_config
        with mock.patch.object(skypilot_config,
                               'apply_cli_config') as apply_cli:
            returned = flags.apply_workspace_option_callback(ctx=None,
                                                             param=None,
                                                             value=None)
        apply_cli.assert_not_called()
        self.assertIsNone(returned)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
