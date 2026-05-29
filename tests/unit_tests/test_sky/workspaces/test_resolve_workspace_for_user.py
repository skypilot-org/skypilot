"""Unit tests for the per-user workspace resolver.

Covers the precedence matrix of `resolve_workspace_for_user()`, the
RBAC-validated `set_user_preferred_workspace()` setter, and the launch-path
trigger predicate `skypilot_config.is_active_workspace_set()`.

Each test asserts the *specific* resolved workspace + source (or the
specific exception type), so reverting any branch of the resolver flips
the test to a different outcome — the revert-check guardrail.
"""
import unittest
from unittest import mock

import pytest

from sky import exceptions
from sky import models
from sky import skypilot_config
from sky.skylet import constants
from sky.workspaces import core as workspaces_core

# Helper -----------------------------------------------------------------


def _patch_resolver(accessible, workspaces=None):
    """Stack the patches every resolver test needs.

    `preferred` is now carried on the User dataclass (populated upstream
    by add_or_update_user); tests set it directly on `self.user` before
    calling the resolver. See the `preferred=...` arg on each test.

    accessible -- iterable of workspace names the user can access.
    workspaces -- workspace dict; defaults to {ws: {} for ws in accessible}
                  plus a 'default' entry to mirror what _load_workspaces
                  synthesizes server-side.
    """
    if workspaces is None:
        workspaces = {ws: {} for ws in accessible}
        workspaces.setdefault(constants.SKYPILOT_DEFAULT_WORKSPACE, {})
    return [
        mock.patch.object(workspaces_core,
                          '_load_workspaces',
                          return_value=workspaces),
        mock.patch.object(workspaces_core,
                          '_accessible_workspace_names_for_user',
                          return_value=set(accessible)),
    ]


def _user_with_preferred(preferred):
    """Build the standard test User with `preferred_workspace` set."""
    return models.User(id='hailong',
                       name='hailong',
                       preferred_workspace=preferred)


class _Patcher:
    """Convenience context manager for stacking multiple mock.patches."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


# Resolver precedence ----------------------------------------------------


class TestResolverPrecedence(unittest.TestCase):
    """Exercises every branch of resolve_workspace_for_user."""

    def setUp(self):
        self.user = models.User(id='hailong', name='hailong')

    def test_explicit_requested_wins(self):
        """`requested` is honored over preferred and accessibility heuristics."""
        user = _user_with_preferred('team-b')
        with _Patcher(_patch_resolver(['team-a', 'team-b'])), \
             mock.patch.object(workspaces_core, 'check_workspace_permission'):
            r = workspaces_core.resolve_workspace_for_user(user,
                                                           requested='team-a')
        self.assertEqual(r.workspace, 'team-a')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_EXPLICIT)

    def test_explicit_requested_validates_access(self):
        """A requested workspace the user can't access raises."""
        with _Patcher(_patch_resolver(['team-a'])), \
             mock.patch.object(
                 workspaces_core,
                 'check_workspace_permission',
                 side_effect=exceptions.PermissionDeniedError('nope')):
            with self.assertRaises(exceptions.PermissionDeniedError):
                workspaces_core.resolve_workspace_for_user(
                    self.user, requested='team-locked')

    def test_preferred_when_accessible(self):
        """Preferred wins over default-fallback."""
        user = _user_with_preferred('team-a')
        with _Patcher(
                _patch_resolver(
                    [constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-a'])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, 'team-a')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_PREFERRED)
        self.assertIsNone(r.note)

    def test_preferred_revoked_falls_through_with_note(self):
        """RBAC drift: preferred no longer accessible -> falls through to
        default-fallback with a `note` explaining the drift."""
        user = _user_with_preferred('team-a')
        with _Patcher(
                _patch_resolver(
                    [constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-b'])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        self.assertIsNotNone(r.note)
        self.assertIn('team-a', r.note)

    def test_single_membership_auto_select(self):
        """Revert-check guardrail. Fixture user has access ONLY to
        'team-only', and 'default' is NOT in their accessible set. Reverting
        the single-membership branch would either AMBIGUOUS (multiple) or
        NoWorkspaceAccessError (zero), both of which flip the assertion.
        """
        with _Patcher(_patch_resolver(['team-only'])):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, 'team-only')
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_SINGLE_MEMBERSHIP)

    def test_zero_accessible_raises_no_workspace_access(self):
        with _Patcher(_patch_resolver([])):
            with self.assertRaises(exceptions.NoWorkspaceAccessError):
                workspaces_core.resolve_workspace_for_user(self.user)

    def test_ambiguous_raises_when_no_default_access(self):
        """Multi-workspace + no preferred + no 'default' access -> AMBIGUOUS.
        Critical fixture detail: 'default' must NOT be in accessible —
        otherwise the default-fallback branch wins and AMBIGUOUS never fires.
        """
        with _Patcher(_patch_resolver(['team-a', 'team-b', 'team-c'])):
            with self.assertRaises(exceptions.WorkspaceAmbiguousError) as cm:
                workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(cm.exception.accessible,
                         ['team-a', 'team-b', 'team-c'])
        # Guidance message is what the CLI/server surface to the user.
        self.assertIn('sky workspace use', str(cm.exception))

    def test_ambiguous_with_drift_note(self):
        """AMBIGUOUS carries the drift note when preferred was revoked."""
        user = _user_with_preferred('team-revoked')
        with _Patcher(_patch_resolver(['team-a', 'team-b', 'team-c'])):
            with self.assertRaises(exceptions.WorkspaceAmbiguousError) as cm:
                workspaces_core.resolve_workspace_for_user(user)
        self.assertIsNotNone(cm.exception.note)
        self.assertIn('team-revoked', cm.exception.note)

    def test_default_fallback_when_accessible(self):
        """Back-compat preservation: legacy multi-ws user who has access to
        'default' lands on 'default', NOT AMBIGUOUS. Without this branch,
        every existing multi-workspace user would break on upgrade.
        """
        with _Patcher(
                _patch_resolver(
                    [constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-a',
                     'team-b'])):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        self.assertIsNone(r.note)

    def test_admin_lands_on_default_no_ambiguous(self):
        """Admin = access to all workspaces. Without the default-fallback
        branch, every admin request would AMBIGUOUS — including every
        daemon tick (system user is admin). This asserts admins keep
        today's UX.
        """
        all_workspaces = [
            constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-a', 'team-b', 'team-c'
        ]
        with _Patcher(_patch_resolver(all_workspaces)):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)

    # ---- Gap-fill cases from the function-test matrix --------

    def test_single_accessible_default_only(self):
        """The default-fallback branch precedes the single-membership branch;
        when the lone accessible workspace IS 'default', the resolver must
        report `default-fallback`, not `single-membership`. Documents the
        precedence order between these two cheap branches."""
        with _Patcher(_patch_resolver([constants.SKYPILOT_DEFAULT_WORKSPACE])):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)

    def test_preferred_default_single_accessible_default(self):
        """preferred='default' explicit overrides the default-fallback
        label; the source must be `preferred` (the user actually chose it)
        not `default-fallback` (the implicit safety net). Surfacing this
        distinction matters in `sky api info` — drives the "your default
        is set" UX vs the "discover sky workspace use" hint."""
        user = _user_with_preferred(constants.SKYPILOT_DEFAULT_WORKSPACE)
        with _Patcher(_patch_resolver([constants.SKYPILOT_DEFAULT_WORKSPACE])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_PREFERRED)

    def test_preferred_inaccessible_single_accessible_default(self):
        """Preferred revoked, only 'default' left → default-fallback with a
        drift note. Validates that the drift note survives onto the
        default-fallback branch (not only the single-membership branch)."""
        user = _user_with_preferred('team-revoked')
        with _Patcher(_patch_resolver([constants.SKYPILOT_DEFAULT_WORKSPACE])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        self.assertIsNotNone(r.note)
        self.assertIn('team-revoked', r.note)

    def test_preferred_matches_single_non_default(self):
        """Preferred is the user's only ws, non-default → use it via
        `preferred` (not `single-membership`). The preferred branch wins
        over the single-membership branch."""
        user = _user_with_preferred('team-only')
        with _Patcher(_patch_resolver(['team-only'])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, 'team-only')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_PREFERRED)

    def test_preferred_mismatch_single_non_default_with_drift(self):
        """Preferred set but inaccessible, the only accessible is a
        different non-default ws → single-membership wins WITH the drift
        note. Verifies the note plumbs through the single-membership
        branch when default is not in the picture."""
        user = _user_with_preferred('team-revoked')
        with _Patcher(_patch_resolver(['team-only'])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, 'team-only')
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_SINGLE_MEMBERSHIP)
        self.assertIsNotNone(r.note)
        self.assertIn('team-revoked', r.note)

    def test_preferred_accessible_no_default_multi(self):
        """Multi ws, no 'default' in accessible, preferred IS accessible →
        use preferred (no AMBIGUOUS). Closes the "preferred wins over
        AMBIGUOUS-trigger" branch — without it the user would still get an
        AMBIGUOUS error despite having explicitly chosen one of their
        accessible workspaces."""
        user = _user_with_preferred('team-b')
        with _Patcher(_patch_resolver(['team-a', 'team-b', 'team-c'])):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, 'team-b')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_PREFERRED)

    def test_preferred_set_zero_accessible(self):
        """Edge: preferred set but the user has zero accessible workspaces
        at all (full RBAC revocation). Falls through to NoWorkspaceAccess.
        Drift note is silently dropped (the base error message is
        sufficient for this rare case)."""
        user = _user_with_preferred('team-revoked')
        with _Patcher(_patch_resolver([])):
            with self.assertRaises(exceptions.NoWorkspaceAccessError):
                workspaces_core.resolve_workspace_for_user(user)

    def test_explicit_active_workspace_ignores_preferred(self):
        """High-priority: when active_workspace is explicitly set (here via
        the `requested` argument, equivalent to the wire-level explicit
        signal), preferred MUST be ignored regardless of whether it's
        set, and the source MUST be `explicit`. Without this assertion a
        regression that started consulting preferred even on the explicit
        path would silently change everyone's behavior."""
        user = _user_with_preferred('team-b')
        with _Patcher(_patch_resolver(['team-a', 'team-b'])), \
             mock.patch.object(workspaces_core, 'check_workspace_permission'):
            r = workspaces_core.resolve_workspace_for_user(user,
                                                           requested='team-a')
        self.assertEqual(r.workspace, 'team-a')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_EXPLICIT)
        # No drift note when explicit — drift only arises from the
        # automatic resolution path.
        self.assertIsNone(r.note)

    def test_admin_single_workspace(self):
        """Admin with a single workspace (degenerate; admin usually sees
        many). The default-fallback branch still wins if the one ws is
        'default'; for a single non-default ws, single-membership fires.
        Documents that the admin path is just the user path with a fuller
        accessible set — no special-casing in the resolver."""
        # Sub-case 1: single ws = default
        with _Patcher(_patch_resolver([constants.SKYPILOT_DEFAULT_WORKSPACE])):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, constants.SKYPILOT_DEFAULT_WORKSPACE)
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        # Sub-case 2: single ws non-default (admin happens to see only one)
        with _Patcher(_patch_resolver(['team-only-admin'])):
            r = workspaces_core.resolve_workspace_for_user(self.user)
        self.assertEqual(r.workspace, 'team-only-admin')
        self.assertEqual(r.source,
                         workspaces_core.WORKSPACE_SOURCE_SINGLE_MEMBERSHIP)

    def test_admin_with_preferred_uses_preferred(self):
        """Admin who has set a preferred → preferred wins over
        default-fallback even though admin can access everything. Verifies
        admins can opt out of the default-fallback path by setting their
        own preference, same as regular users."""
        all_workspaces = [
            constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-a', 'team-b'
        ]
        user = _user_with_preferred('team-b')
        with _Patcher(_patch_resolver(all_workspaces)):
            r = workspaces_core.resolve_workspace_for_user(user)
        self.assertEqual(r.workspace, 'team-b')
        self.assertEqual(r.source, workspaces_core.WORKSPACE_SOURCE_PREFERRED)


# Preferred-workspace setter RBAC ----------------------------------------


class TestPreferredSetterRBAC(unittest.TestCase):

    def setUp(self):
        self.user = models.User(id='hailong', name='hailong')

    def test_setter_rejects_inaccessible_ws(self):
        with mock.patch.object(workspaces_core, '_load_workspaces',
                               return_value={'team-a': {}, 'team-b': {}}), \
             mock.patch.object(
                 workspaces_core, 'check_workspace_permission',
                 side_effect=exceptions.PermissionDeniedError('denied')), \
             mock.patch.object(workspaces_core.global_user_state,
                               'set_user_preferred_workspace') as set_db:
            with self.assertRaises(exceptions.PermissionDeniedError):
                workspaces_core.set_user_preferred_workspace(
                    self.user, 'team-a')
            set_db.assert_not_called()

    def test_setter_rejects_unknown_workspace(self):
        """Workspace must exist before permission is even checked."""
        with mock.patch.object(workspaces_core, '_load_workspaces',
                               return_value={'team-a': {}}), \
             mock.patch.object(workspaces_core,
                               'check_workspace_permission') as check, \
             mock.patch.object(workspaces_core.global_user_state,
                               'set_user_preferred_workspace') as set_db:
            with self.assertRaises(ValueError) as cm:
                workspaces_core.set_user_preferred_workspace(
                    self.user, 'team-unknown')
            self.assertIn('does not exist', str(cm.exception))
            check.assert_not_called()
            set_db.assert_not_called()

    def test_setter_clear_always_succeeds(self):
        """`None` (clear) bypasses both existence and permission checks."""
        with mock.patch.object(workspaces_core, '_load_workspaces') as load, \
             mock.patch.object(workspaces_core,
                               'check_workspace_permission') as check, \
             mock.patch.object(workspaces_core.global_user_state,
                               'set_user_preferred_workspace') as set_db:
            workspaces_core.set_user_preferred_workspace(self.user, None)
            load.assert_not_called()
            check.assert_not_called()
            set_db.assert_called_once_with(self.user.id, None)

    def test_setter_persists_via_db_layer(self):
        with mock.patch.object(workspaces_core, '_load_workspaces',
                               return_value={'team-a': {}}), \
             mock.patch.object(workspaces_core,
                               'check_workspace_permission'), \
             mock.patch.object(workspaces_core.global_user_state,
                               'set_user_preferred_workspace') as set_db:
            workspaces_core.set_user_preferred_workspace(self.user, 'team-a')
            set_db.assert_called_once_with(self.user.id, 'team-a')


# Launch-path trigger predicate -----------------------------------------


class TestIsActiveWorkspaceSet(unittest.TestCase):
    """is_active_workspace_set() is the gate that decides whether the
    server-side resolver runs. It MUST distinguish 'user-set' from
    'fell-back-to-default-literal'.
    """

    def _clear_context(self):
        # The thread-local active workspace context can leak across tests if
        # not explicitly reset (some tests above may have set it).
        if hasattr(skypilot_config._active_workspace_context, 'workspace'):
            skypilot_config._active_workspace_context.workspace = None

    def setUp(self):
        self._clear_context()
        self.addCleanup(self._clear_context)

    def test_returns_false_when_nothing_set(self):
        """Trigger fires (returns False -> resolver runs) when neither
        thread-local context nor merged config sets active_workspace."""
        with mock.patch.object(skypilot_config, 'get_nested',
                               return_value=None):
            self.assertFalse(skypilot_config.is_active_workspace_set())

    def test_returns_true_when_set_to_default(self):
        """Trigger does NOT fire (returns True -> use as-is) when the user
        explicitly set active_workspace='default'. Explicit intent must be
        respected; no silent resolver-rescue."""
        with mock.patch.object(skypilot_config,
                               'get_nested',
                               return_value='default'):
            self.assertTrue(skypilot_config.is_active_workspace_set())

    def test_returns_true_when_set_to_non_default(self):
        with mock.patch.object(skypilot_config,
                               'get_nested',
                               return_value='team-a'):
            self.assertTrue(skypilot_config.is_active_workspace_set())

    def test_returns_true_for_thread_local_context(self):
        """The CLI --workspace flag sets the thread-local context. The
        trigger MUST see it and treat it as explicit, otherwise the
        server resolver would clobber --workspace with the user's
        preferred."""
        skypilot_config._active_workspace_context.workspace = 'team-from-flag'
        try:
            with mock.patch.object(skypilot_config,
                                   'get_nested',
                                   return_value=None):
                self.assertTrue(skypilot_config.is_active_workspace_set())
        finally:
            skypilot_config._active_workspace_context.workspace = None


# Exception class signatures --------------------------------------------


class TestWorkspaceAmbiguousErrorSignature(unittest.TestCase):
    """The note kwarg was added late (review fix). Existing call sites that
    pass only `accessible` must keep working — protects against accidental
    signature breakage on a future refactor."""

    def test_construct_without_note_keeps_working(self):
        e = exceptions.WorkspaceAmbiguousError(['a', 'b'])
        self.assertEqual(e.accessible, ['a', 'b'])
        self.assertIsNone(e.note)
        self.assertNotIn('Note:', str(e))

    def test_construct_with_note_includes_explanation(self):
        e = exceptions.WorkspaceAmbiguousError(
            ['a', 'b'], note="preferred 'x' not accessible")
        self.assertEqual(e.note, "preferred 'x' not accessible")
        self.assertIn('Note:', str(e))
        self.assertIn("preferred 'x' not accessible", str(e))

    def test_accessible_is_sorted(self):
        """Tests assert exact ordering elsewhere; sorting must be stable."""
        e = exceptions.WorkspaceAmbiguousError(['c', 'a', 'b'])
        self.assertEqual(e.accessible, ['a', 'b', 'c'])


class TestNoWorkspaceAccessError(unittest.TestCase):
    """NoWorkspaceAccessError must remain a PermissionDeniedError subclass
    so existing `except PermissionDeniedError` handlers keep catching the
    zero-accessible case."""

    def test_is_permission_denied_subclass(self):
        self.assertTrue(
            issubclass(exceptions.NoWorkspaceAccessError,
                       exceptions.PermissionDeniedError))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
