"""Unit tests for POST /users/me/workspace and the executor's resolver
injection gate (daemon-skip + client API version gate).

The endpoint is synchronous (returns JSON directly, not the request-queue
pattern), so it can be exercised by calling the route handler with a
fake fastapi.Request — no full test client setup needed.

Note: there is no GET counterpart. The user's preferred_workspace is
returned inline on the User row of /api/health (the User dataclass
includes the column), so a dedicated GET would just duplicate that.
"""
import unittest
from unittest import mock

import fastapi
import pytest

from sky import exceptions
from sky import models
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.users import server as users_server
from sky.workspaces import core as workspaces_core


def _fake_request(auth_user):
    req = mock.MagicMock(spec=fastapi.Request)
    req.state.auth_user = auth_user
    return req


# POST /users/me/workspace ---------------------------------------------


class TestSetUsersMeWorkspace(unittest.TestCase):

    def setUp(self):
        self.user = models.User(id='hailong', name='hailong')

    def _body(self, preferred):
        b = mock.MagicMock(spec=payloads.UserPreferredWorkspaceBody)
        b.preferred = preferred
        return b

    def test_accepted_writes_and_echoes_new_value(self):
        """Happy path: server stores the preferred and echoes it back so
        the CLI / dashboard can confirm what landed."""
        with mock.patch.object(workspaces_core,
                               'set_user_preferred_workspace') as set_pref:
            resp = users_server.set_user_preferred_workspace(
                _fake_request(self.user), self._body('team-a'))
        set_pref.assert_called_once_with(self.user, 'team-a')
        self.assertEqual(resp, {'preferred': 'team-a'})

    def test_rejected_no_access_raises_403(self):
        with mock.patch.object(
                workspaces_core,
                'set_user_preferred_workspace',
                side_effect=exceptions.PermissionDeniedError('denied')):
            with self.assertRaises(fastapi.HTTPException) as cm:
                users_server.set_user_preferred_workspace(
                    _fake_request(self.user), self._body('team-locked'))
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn('denied', cm.exception.detail)

    def test_unknown_workspace_raises_404(self):
        with mock.patch.object(workspaces_core,
                               'set_user_preferred_workspace',
                               side_effect=ValueError('does not exist')):
            with self.assertRaises(fastapi.HTTPException) as cm:
                users_server.set_user_preferred_workspace(
                    _fake_request(self.user), self._body('team-unknown'))
        self.assertEqual(cm.exception.status_code, 404)

    def test_clear_with_none_round_trips(self):
        with mock.patch.object(workspaces_core,
                               'set_user_preferred_workspace') as set_pref:
            resp = users_server.set_user_preferred_workspace(
                _fake_request(self.user), self._body(None))
        set_pref.assert_called_once_with(self.user, None)
        self.assertIsNone(resp['preferred'])

    def test_unauth_post_raises_401(self):
        """When auth middleware did not set auth_user, POST is rejected.
        Local dev with auth disabled hits this too — by design, writes
        require an authenticated identity."""
        with self.assertRaises(fastapi.HTTPException) as cm:
            users_server.set_user_preferred_workspace(
                _fake_request(auth_user=None), self._body('team-a'))
        self.assertEqual(cm.exception.status_code, 401)


# Version gate constant -----------------------------------------------


class TestVersionGateConstant(unittest.TestCase):
    """The constant must equal the API_VERSION that introduced it; if a
    later commit bumps API_VERSION without updating this constant, the
    server-side resolver would silently start running for clients that
    don't know about WorkspaceAmbiguousError. This guards against that."""

    def test_constant_matches_introducing_api_version(self):
        self.assertEqual(server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION,
                         53)
        # Must be <= current API_VERSION; otherwise old servers would never
        # serve clients (a value > API_VERSION makes no sense).
        self.assertLessEqual(
            server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION,
            server_constants.API_VERSION)


# Executor resolver-gate ----------------------------------------------


class TestExecutorResolverGate(unittest.TestCase):
    """Verify _should_apply_workspace_resolver() gates the per-user
    resolver correctly for the three skip-cases (daemon / old client /
    explicit active_workspace) AND fires for the normal case."""

    def setUp(self):
        from sky.server.requests import executor
        self.executor = executor

    def _patch_gates(self, *, api_version, is_active_workspace_set):
        from sky import skypilot_config
        from sky.server import versions
        return [
            mock.patch.object(versions,
                              'get_remote_api_version',
                              return_value=api_version),
            mock.patch.object(skypilot_config,
                              'is_active_workspace_set',
                              return_value=is_active_workspace_set),
        ]

    def _call(self, is_daemon: bool, api_version, active_workspace_set: bool):
        for p in self._patch_gates(
                api_version=api_version,
                is_active_workspace_set=active_workspace_set):
            p.start()
        try:
            return self.executor._should_apply_workspace_resolver(is_daemon)
        finally:
            mock.patch.stopall()

    def test_daemon_request_skips_resolver(self):
        """Daemon = system user = admin = would always AMBIGUOUS on
        multi-workspace servers. Skipping is correctness AND a perf win."""
        self.assertFalse(
            self._call(is_daemon=True,
                       api_version=server_constants.API_VERSION,
                       active_workspace_set=False))

    def test_old_client_skips_resolver(self):
        """API version below the introducing version → preserve legacy
        behavior. Old clients wouldn't know how to format the new error."""
        old_version = (server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION - 1)
        self.assertFalse(
            self._call(is_daemon=False,
                       api_version=old_version,
                       active_workspace_set=False))

    def test_unknown_client_version_skips_resolver(self):
        """If versions.get_remote_api_version() returns None (no header),
        be conservative — don't run the resolver."""
        self.assertFalse(
            self._call(is_daemon=False,
                       api_version=None,
                       active_workspace_set=False))

    def test_explicit_active_workspace_skips_resolver(self):
        """User explicitly named a workspace → respect it; preferred MUST
        be ignored."""
        self.assertFalse(
            self._call(is_daemon=False,
                       api_version=server_constants.API_VERSION,
                       active_workspace_set=True))

    def test_new_client_no_explicit_runs_resolver(self):
        """The happy path: non-daemon + new client + nothing explicit set
        → the resolver runs."""
        self.assertTrue(
            self._call(is_daemon=False,
                       api_version=server_constants.API_VERSION,
                       active_workspace_set=False))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
