"""Compatibility matrix: old API server vs new API server, per resolver case.

Why this is pure UT (no dual-server, no real launch):
  - "Old" server behavior is deterministic: `active_workspace if set else
    'default'`, then 403 if the user has no access. Easy to simulate in 3
    lines.
  - "New" server behavior is `resolve_workspace_for_user()` — the same
    function the launch path calls. No need to actually launch a cluster
    or hit `sky api info`; calling the function directly is the truth.

This test:
  1. Enumerates every cell of the function-test matrix (server & client
     active_workspace × preferred × accessible-workspaces).
  2. Runs both the simulated old behavior and the real new resolver.
  3. Prints a markdown comparison table (visible with `-s`).
  4. Asserts the **backward-compat invariant**: every case where the old
     server returned a workspace successfully MUST also succeed on the
     new server. When the user has not opted in (no preferred set), the
     new server MUST also pick the same workspace as the old server.
     Cases where the old server errored may now succeed (the auto-select
     win) — those are documented diffs, not regressions.
"""
import io
from typing import List, NamedTuple, Optional, Set
import unittest
from unittest import mock

import pytest

from sky import exceptions
from sky import models
from sky.skylet import constants
from sky.workspaces import core as workspaces_core

# Workspaces defined on the server. Some test users have access to subsets
# of these to model RBAC variance.
ALL_WORKSPACES = {
    constants.SKYPILOT_DEFAULT_WORKSPACE: {},
    'team-a': {},
    'team-b': {},
    'team-c': {},
    'team-x': {},  # used as a "revoked / inaccessible" target
}

# ---------- "old" server behavior (before per-user resolver) -----------


class OldResult(NamedTuple):
    """What the old server would do. Either errors or returns a workspace."""
    error: Optional[str]  # 'PermissionDenied' or None
    workspace: Optional[str]


def simulate_old_resolver(active_workspace: Optional[str],
                          accessible: Set[str]) -> OldResult:
    """Old behavior: fall back to 'default' literal when active_workspace is
    not set; validate access; 403 on no access."""
    ws = (active_workspace if active_workspace is not None else
          constants.SKYPILOT_DEFAULT_WORKSPACE)
    if ws not in accessible:
        return OldResult(error='PermissionDenied', workspace=None)
    return OldResult(error=None, workspace=ws)


# ---------- "new" server behavior (resolver) ---------------------------


class NewResult(NamedTuple):
    error: Optional[str]  # exception class name or None
    workspace: Optional[str]
    source: Optional[str]
    note: Optional[str]


def run_new_resolver(active_workspace: Optional[str], preferred: Optional[str],
                     accessible: Set[str]) -> NewResult:
    """Drive resolve_workspace_for_user() with the given state."""
    # The resolver reads preferred from the User dataclass (populated
    # upstream by add_or_update_user); set it directly on the fixture user.
    user = models.User(id='compat',
                       name='compat',
                       preferred_workspace=preferred)
    patches = [
        mock.patch.object(workspaces_core,
                          '_load_workspaces',
                          return_value=ALL_WORKSPACES),
        mock.patch.object(workspaces_core,
                          '_accessible_workspace_names_for_user',
                          return_value=set(accessible)),
    ]

    # check_workspace_permission gets called when `requested` is set; mirror
    # the real RBAC: pass if requested in accessible, raise otherwise.
    def _perm_check(_user, ws):
        if ws not in accessible:
            raise exceptions.PermissionDeniedError(
                f'no access to workspace {ws!r}')

    patches.append(
        mock.patch.object(workspaces_core,
                          'check_workspace_permission',
                          side_effect=_perm_check))
    for p in patches:
        p.start()
    try:
        try:
            r = workspaces_core.resolve_workspace_for_user(
                user, requested=active_workspace)
            return NewResult(error=None,
                             workspace=r.workspace,
                             source=r.source,
                             note=r.note)
        except exceptions.NoWorkspaceAccessError:
            return NewResult(error='NoWorkspaceAccess',
                             workspace=None,
                             source=None,
                             note=None)
        except exceptions.WorkspaceAmbiguousError as e:
            return NewResult(error='WorkspaceAmbiguous',
                             workspace=None,
                             source=None,
                             note=e.note)
        except exceptions.PermissionDeniedError:
            return NewResult(error='PermissionDenied',
                             workspace=None,
                             source=None,
                             note=None)
    finally:
        for p in patches:
            p.stop()


# ---------- the matrix -------------------------------------------------


class Case(NamedTuple):
    case_id: str
    # active_workspace effective value — collapses "set in server" vs
    # "set in client .sky.yaml". Both routes converge here.
    active_workspace: Optional[str]
    preferred: Optional[str]
    accessible: List[str]


# Accessible-workspaces variants matching the 5 user-role rows plus the 2
# admin rows (admin = has access to all configured workspaces).
ACCESSIBLE_VARIANTS = {
    'user_0': [],
    'user_1default': [constants.SKYPILOT_DEFAULT_WORKSPACE],
    'user_1nondef': ['team-a'],
    'user_N+def': [constants.SKYPILOT_DEFAULT_WORKSPACE, 'team-a', 'team-b'],
    'user_N-def': ['team-a', 'team-b', 'team-c'],
    'admin_1': [constants.SKYPILOT_DEFAULT_WORKSPACE],
    'admin_N': list(ALL_WORKSPACES.keys()),
}

MATRIX: List[Case] = []
for acc_id, acc_list in ACCESSIBLE_VARIANTS.items():
    # 4 client × server combos collapse to 4 distinct "effective" states:
    # nothing / aw / pref / both. Plus we vary aw inside vs outside accessible
    # to cover the "explicit-but-no-access" branch.
    pick_in = (
        [w for w in acc_list if w != constants.SKYPILOT_DEFAULT_WORKSPACE] or
        acc_list)
    aw_in_acc = pick_in[0] if pick_in else None
    aw_out_acc = 'team-x'  # never in any test user's accessible set
    pref_in_acc = aw_in_acc
    pref_out_acc = 'team-x'

    MATRIX.extend([
        Case(f'{acc_id}|no-aw|no-pref', None, None, acc_list),
        Case(f'{acc_id}|no-aw|pref-accessible', None, pref_in_acc, acc_list),
        Case(f'{acc_id}|no-aw|pref-inaccessible', None, pref_out_acc, acc_list),
        Case(f'{acc_id}|aw-accessible|no-pref', aw_in_acc, None, acc_list),
        Case(f'{acc_id}|aw-accessible|pref-set', aw_in_acc, pref_out_acc,
             acc_list),
        Case(f'{acc_id}|aw-inaccessible|no-pref', aw_out_acc, None, acc_list),
    ])

# Skip cases where aw_in_acc is None (variant has zero accessible workspaces).
MATRIX = [
    c for c in MATRIX
    if (c.active_workspace is None or c.active_workspace == 'team-x' or
        c.active_workspace in c.accessible)
]

# ---------- the actual test --------------------------------------------


class TestResolverCompatMatrix(unittest.TestCase):
    """Replaces what would otherwise be a dual-server compat smoke. All
    matrix cells run as pure UT — fast, deterministic, no setup."""

    def test_compat_matrix(self):
        """Run every cell through old + new; print table; assert
        backward-compat invariants."""
        rows = []
        regressions = []
        for case in MATRIX:
            accessible_set = set(case.accessible)
            old = simulate_old_resolver(case.active_workspace, accessible_set)
            new = run_new_resolver(case.active_workspace, case.preferred,
                                   accessible_set)
            # Diff classification (for the human-readable table):
            if old.error is None and new.error is None:
                if old.workspace == new.workspace:
                    diff = 'SAME'
                elif case.preferred is not None:
                    # Different workspace because the user OPTED IN by
                    # setting preferred — the new behavior is the feature.
                    diff = (f'OPT-IN: preferred={case.preferred} '
                            f'overrides legacy {old.workspace} -> '
                            f'{new.workspace}')
                else:
                    # Different workspace WITHOUT the user opting in — a
                    # silent change. The invariant assertion below catches
                    # this; here we just label.
                    diff = (f'SILENT-CHANGE: old={old.workspace} '
                            f'new={new.workspace}')
            elif old.error is not None and new.error is None:
                diff = f'IMPROVED: was {old.error}, now picks {new.workspace}'
            elif old.error is None and new.error is not None:
                diff = (f'AVAILABILITY-REGRESSION: old picked '
                        f'{old.workspace}, now {new.error}')
                regressions.append((case, old, new))
            else:
                # Both errored; new may give a more actionable error
                # (WorkspaceAmbiguous with guidance) vs old's blunt 403.
                if old.error == new.error:
                    diff = 'SAME-ERROR'
                else:
                    diff = (f'BETTER-ERROR: {old.error} -> {new.error} '
                            '(more actionable)')
            rows.append((case, old, new, diff))

            # Backward-compat invariant (relaxed for opt-in cases):
            #
            #   (a) "Availability": if old returned a workspace, new MUST
            #       also return a workspace (not raise). This is the
            #       hard-no-regression guarantee — upgrading the server
            #       cannot make a previously-working launch start failing.
            #
            #   (b) "Same-workspace stability": when the user has NOT set
            #       preferred (i.e. has not opted into the new behavior),
            #       new MUST pick the same workspace as old. Setting a
            #       preferred is an explicit opt-in to "use this instead
            #       of the legacy 'default' fallback" — diverging there is
            #       intentional, not a regression.
            if old.error is None:
                self.assertIsNone(
                    new.error,
                    f'AVAILABILITY REGRESSION on {case.case_id}: old '
                    f'returned {old.workspace}, new raises {new.error}')
                if case.preferred is None:
                    self.assertEqual(
                        new.workspace, old.workspace,
                        f'SILENT BEHAVIOR CHANGE on {case.case_id} '
                        f'(no preferred set): old picked '
                        f'{old.workspace}, new picked {new.workspace}')

        # Always print the comparison table (visible with `pytest -s`).
        out = io.StringIO()
        out.write('\n\n## Resolver compat matrix\n\n')
        out.write('| Case | active_workspace | preferred | accessible | '
                  'OLD result | NEW result | Diff |\n')
        out.write('|---|---|---|---|---|---|---|\n')
        for case, old, new, diff in rows:
            old_str = (f'FAIL {old.error}'
                       if old.error else f'OK {old.workspace}')
            if new.error:
                new_str = f'FAIL {new.error}'
            else:
                new_str = f'OK {new.workspace} ({new.source})'
                if new.note:
                    new_str += f' / note: {new.note}'
            out.write(f'| `{case.case_id}` | '
                      f'`{case.active_workspace or "-"}` | '
                      f'`{case.preferred or "-"}` | '
                      f'`{case.accessible}` | '
                      f'{old_str} | {new_str} | {diff} |\n')
        print(out.getvalue())

        # Hard guarantee:
        self.assertEqual(
            regressions, [], f'Backward-compat regressions detected: '
            f'{regressions}')


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
