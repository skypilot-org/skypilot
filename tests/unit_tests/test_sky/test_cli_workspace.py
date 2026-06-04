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

    def test_ambiguous_message_scopes_workspace_flag_to_launch(self):
        """`--workspace <name>` only exists on `sky launch` / `sky jobs
        launch`. The message must NOT advertise it as a universal fix —
        users running `sky status` / `sky queue` / etc. would try a flag
        that doesn't exist on their command. The wording was changed
        deliberately to push `--workspace` into a footnote that names
        the two commands where it applies."""
        e = exceptions.WorkspaceAmbiguousError(['team-a', 'team-b'])
        msg = str(e)
        # The launch-specific footnote must mention BOTH launch commands.
        self.assertIn('sky launch', msg)
        self.assertIn('sky jobs launch', msg)
        # The universal fix list (`sky workspace use` + `~/.sky/config.yaml`)
        # must not include `--workspace` — that would be misleading.
        # Grab the substring before the launch-specific footnote and
        # assert `--workspace` isn't in it.
        footnote_marker = 'for a one-shot override'
        self.assertIn(
            footnote_marker, msg,
            f'Expected launch footnote marker {footnote_marker!r}: {msg}')
        universal_section = msg.split(footnote_marker)[0]
        self.assertNotIn(
            '--workspace', universal_section,
            f'`--workspace` leaked into the universal fix list: '
            f'{universal_section!r}')

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


class TestSkyWorkspaceInfoCommand(unittest.TestCase):
    """`sky workspace info` is the read counterpart to `sky workspace use`
    — pure CLI rendering on top of `sdk.get_user_workspace()`. The
    server-side handler is covered in
    `test_preferred_workspace_endpoints.py`; here we just check the
    table/JSON output shape so a regression in CLI formatting is caught
    without spinning up the whole server."""

    def setUp(self):
        self.runner = CliRunner()
        self.payload = {
            'workspace': 'team-a',
            'source': 'preferred',
            'note': None,
            'preferred': 'team-a',
            'accessible': ['default', 'team-a', 'team-b'],
        }

    def test_table_output_lists_workspace_source_preferred_accessible(self):
        with mock.patch.object(sdk,
                               'get_user_workspace',
                               return_value=self.payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        # Default table mode prints every field the JSON shape carries.
        # We do exact-substring checks rather than line-numbered ones so a
        # cosmetic shuffle (e.g. swapping two lines) won't tank the test.
        self.assertIn("Workspace: 'team-a'", result.output)
        self.assertIn('Source: preferred', result.output)
        self.assertIn("Preferred: 'team-a'", result.output)
        self.assertIn("'default', 'team-a', 'team-b'", result.output)
        # No drift note in this payload — must not render a 'Note:' line.
        self.assertNotIn('Note:', result.output)

    def test_table_renders_note_when_present(self):
        """RBAC drift: preferred was set but the user lost access. The
        note explaining the drift is part of the user-facing contract;
        suppressing it would hide why the resolver picked something other
        than what the user configured."""
        payload = {
            **self.payload,
            'workspace': 'default',
            'source': 'default-fallback',
            'note': "preferred 'team-a' not accessible",
        }
        with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Note: preferred 'team-a' not accessible", result.output)

    def test_json_output_is_passthrough(self):
        """`-o json` must surface the payload verbatim so scripts can
        parse the contract without depending on the text format."""
        with mock.patch.object(sdk,
                               'get_user_workspace',
                               return_value=self.payload):
            result = self.runner.invoke(command.cli,
                                        ['workspace', 'info', '-o', 'json'])
        self.assertEqual(result.exit_code, 0, result.output)
        import json as _json
        parsed = _json.loads(result.output)
        self.assertEqual(parsed, self.payload)

    def test_unset_preferred_renders_as_not_set(self):
        """User hasn't called `sky workspace use` yet — `preferred` is
        None on the wire. The table must distinguish "(not set)" from
        the literal string 'None' so users can tell empty from
        misconfigured."""
        payload = {
            **self.payload,
            'preferred': None,
            'workspace': 'default',
            'source': 'default-fallback',
        }
        with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Preferred: (not set)', result.output)

    def test_ambiguous_source_appends_recovery_hint_below_tree(self):
        """When the resolver couldn't pick a workspace, the handler
        returns `source='ambiguous'` with a SHORT `note`. The CLI must
        render the tree uniformly (so `Preferred` / `Accessible` aren't
        pushed sideways by a multi-line `Note:`), then append the long
        recovery guidance as a separate block below. The hint text
        comes from `WorkspaceAmbiguousError.recovery_hint()` so the
        CLI and the launch-path exception message stay in sync.

        Revert check: drop the appended `recovery_hint()` echo in
        `workspace_info` and the `sky workspace use` substring assertion
        below fails (the hint lives only in the appended paragraph,
        never in `note`)."""
        payload = {
            'workspace': None,
            'source': 'ambiguous',
            'note': 'multiple workspaces accessible; '
                    'no preferred or active workspace set',
            'preferred': None,
            'accessible': ['team-a', 'team-b'],
        }
        with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        # Tree row carries only the SHORT note — no multi-line spill.
        self.assertIn('Source: ambiguous', result.output)
        self.assertIn('Note: multiple workspaces accessible', result.output)
        # The recovery hint appears as a separate paragraph below.
        self.assertIn(exceptions.WorkspaceAmbiguousError.recovery_hint(),
                      result.output)
        # The hint must follow the tree, not be embedded in it.
        accessible_idx = result.output.index('Accessible:')
        hint_idx = result.output.index('sky workspace use')
        self.assertLess(
            accessible_idx, hint_idx,
            'Recovery hint must come AFTER the tree (`Accessible:` row),'
            ' otherwise the tree alignment breaks visually.')

    def test_no_access_source_renders_note_without_extra_hint(self):
        """User has no accessible workspaces. The raise-site note
        ("User <name> (<id>) has no accessible workspaces.") fits in
        the tree row — no separate recovery paragraph is appended.
        The AMBIGUOUS state is special because its hint is multi-line;
        NO_ACCESS doesn't share that problem."""
        payload = {
            'workspace': None,
            'source': 'no-access',
            'note': 'User alice (alice) has no accessible workspaces.',
            'preferred': None,
            'accessible': [],
        }
        with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Source: no-access', result.output)
        self.assertIn('Accessible: (none)', result.output)
        self.assertIn('User alice (alice) has no accessible workspaces.',
                      result.output)
        # No appended recovery paragraph — the note already says it all.
        self.assertNotIn('sky workspace use', result.output)

    def test_permission_denied_source_does_not_append_generic_hint(self):
        """For permission-denied the `note` already names the specific
        workspace + reason. A generic recovery paragraph here would be
        noise on top of the specific message — verify the CLI suppresses
        the append for this state."""
        payload = {
            'workspace': None,
            'source': 'permission-denied',
            'note': "User does not have permission to access workspace "
                    "'team-locked'",
            'preferred': None,
            'accessible': ['team-a'],
        }
        with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
            result = self.runner.invoke(command.cli, ['workspace', 'info'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Source: permission-denied', result.output)
        self.assertIn('team-locked', result.output)
        # No generic recovery paragraph for this state.
        self.assertNotIn('sky workspace use', result.output)
        self.assertNotIn('Ask an admin', result.output)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
