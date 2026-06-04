"""Unit tests for POST /users/me/workspace and the executor's resolver
injection gate (daemon-skip + client API version gate).

The endpoint is synchronous (returns JSON directly, not the request-queue
pattern), so it can be exercised by calling the route handler with a
fake fastapi.Request — no full test client setup needed.
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
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core


def _fake_request(auth_user):
    req = mock.MagicMock(spec=fastapi.Request)
    req.state.auth_user = auth_user
    return req


# POST /users/me/workspace ---------------------------------------------


class TestSetUsersMeWorkspace(unittest.TestCase):

    def setUp(self):
        self.user = models.User(id='alice', name='alice')

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


# GET /users/me/workspace --------------------------------------------


class TestGetUsersMeWorkspace(unittest.TestCase):
    """Coverage for the read counterpart of POST /users/me/workspace.

    Exercises the four shapes the handler is supposed to surface:
      * happy path (resolver picks preferred)
      * resolver drift (preferred set but not accessible -> note populated)
      * resolver returns the same workspace via default-fallback even
        though preferred is None (back-compat path)
      * 401 when no auth user

    The handler is read-only and pure-Python; we mock the DB fetch and
    the resolver helpers rather than spinning up a full app.
    """

    def setUp(self):
        # Middleware-built user: only id+name+type, no preferred_workspace.
        # Matches how AuthProxyMiddleware constructs `auth_user` from a
        # header — which the handler must NOT blindly trust for fields
        # that live in the DB.
        self.auth_user = models.User(id='alice', name='alice')

    def _patch(self, fresh_user, resolution, accessible):
        return [
            mock.patch.object(users_server.global_user_state,
                              'get_user',
                              return_value=fresh_user),
            mock.patch.object(users_server.workspaces_core,
                              'resolve_workspace_for_user',
                              return_value=resolution),
            mock.patch.object(users_server.workspaces_core,
                              'get_accessible_workspace_names',
                              return_value=set(accessible)),
            # Pin the server-side `active_workspace` lookup to "unset"
            # so these tests don't depend on the test machine's actual
            # `~/.sky/config.yaml`. The fallback-to-server-config path
            # is exercised separately in
            # `test_server_active_workspace_falls_back_into_requested`.
            mock.patch.object(users_server.skypilot_config,
                              'is_active_workspace_set',
                              return_value=False),
        ]

    def test_happy_path_preferred(self):
        """Preferred set + accessible -> resolver returns preferred, the
        handler echoes both alongside the accessible set."""
        fresh = models.User(id='alice',
                            name='alice',
                            preferred_workspace='team-a')
        resolution = workspaces_core.WorkspaceResolution(
            workspace='team-a',
            source=workspace_constants.WORKSPACE_SOURCE_PREFERRED)
        patches = self._patch(fresh, resolution, {'team-a', 'team-b'})
        for p in patches:
            p.start()
        try:
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(resp['workspace'], 'team-a')
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_PREFERRED)
        self.assertIsNone(resp['note'])
        self.assertEqual(resp['preferred'], 'team-a')
        # `accessible` is sorted to make the wire format deterministic
        # — the handler sorts the set the permission service returns.
        self.assertEqual(resp['accessible'], ['team-a', 'team-b'])

    def test_drift_preferred_not_accessible(self):
        """Preferred='team-x' but the user lost access (RBAC drift).
        Resolver falls back to default; handler exposes the drift note
        AND echoes the stale preferred so the UI can surface it."""
        fresh = models.User(id='alice',
                            name='alice',
                            preferred_workspace='team-x')
        resolution = workspaces_core.WorkspaceResolution(
            workspace='default',
            source=workspace_constants.WORKSPACE_SOURCE_DEFAULT_FALLBACK,
            note="preferred 'team-x' not accessible")
        patches = self._patch(fresh, resolution, {'default', 'team-a'})
        for p in patches:
            p.start()
        try:
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(resp['workspace'], 'default')
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        self.assertEqual(resp['note'], "preferred 'team-x' not accessible")
        # The persisted preferred is reported even though it's not
        # accessible — the dashboard surfaces this to explain the drift.
        self.assertEqual(resp['preferred'], 'team-x')

    def test_preferred_none_with_default_fallback(self):
        """User has not set preferred but has access to default.
        Returned `preferred` is None (not the resolved fallback), so a
        client can tell "I haven't set anything" from "I set default"."""
        fresh = models.User(id='alice', name='alice')
        resolution = workspaces_core.WorkspaceResolution(
            workspace='default',
            source=workspace_constants.WORKSPACE_SOURCE_DEFAULT_FALLBACK)
        patches = self._patch(fresh, resolution, {'default', 'team-a'})
        for p in patches:
            p.start()
        try:
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(resp['workspace'], 'default')
        self.assertIsNone(resp['preferred'])

    def test_unauth_get_raises_401(self):
        """Reads also require an identity — anonymous probes can't see
        another user's workspace state. Mirrors POST 401 semantics."""
        with self.assertRaises(fastapi.HTTPException) as cm:
            users_server.get_user_workspace(_fake_request(auth_user=None))
        self.assertEqual(cm.exception.status_code, 401)

    def test_requested_passed_through_to_resolver_as_explicit(self):
        """Caller's `--workspace` / client-side `active_workspace` lands
        on the resolver's `requested` param so it picks the EXPLICIT
        precedence branch. Without this, `sky workspace info` would
        contradict `sky launch` for users with an active workspace set
        locally — same machinery, different answer."""
        fresh = models.User(id='alice',
                            name='alice',
                            preferred_workspace='team-a')
        resolution = workspaces_core.WorkspaceResolution(
            workspace='team-b',
            source=workspace_constants.WORKSPACE_SOURCE_EXPLICIT)
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(users_server.workspaces_core,
                               'resolve_workspace_for_user',
                               return_value=resolution) as resolve_mock, \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value={'team-a', 'team-b'}), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=False):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user),
                                                   requested='team-b')
        # Resolver MUST receive `requested='team-b'`, not None.
        resolve_mock.assert_called_once_with(fresh, requested='team-b')
        self.assertEqual(resp['workspace'], 'team-b')
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_EXPLICIT)
        # Preferred is reported alongside even though the explicit
        # override won — the caller may want to surface "you're
        # overriding your saved preference for this one launch".
        self.assertEqual(resp['preferred'], 'team-a')

    def test_server_active_workspace_falls_back_into_requested(self):
        """When the client did NOT pass `?requested=`, the handler picks
        up the server's own `skypilot_config.active_workspace` (rare —
        usually unset, but admins may pin it). Matches launch-path
        precedence: the server's loaded config flows into the
        resolver's `requested` slot."""
        fresh = models.User(id='alice', name='alice')
        resolution = workspaces_core.WorkspaceResolution(
            workspace='server-pinned',
            source=workspace_constants.WORKSPACE_SOURCE_EXPLICIT)
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(users_server.workspaces_core,
                               'resolve_workspace_for_user',
                               return_value=resolution) as resolve_mock, \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value={'server-pinned'}), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=True), \
             mock.patch.object(users_server.skypilot_config,
                               'get_active_workspace',
                               return_value='server-pinned'):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        resolve_mock.assert_called_once_with(fresh, requested='server-pinned')
        self.assertEqual(resp['workspace'], 'server-pinned')

    def test_ambiguous_resolver_surfaces_as_state_coded_source(self):
        """Resolver raises WorkspaceAmbiguousError (multi-ws user, no
        preferred, no 'default' access). The GET handler must NOT dump
        the exception's multi-line recovery text into `note` — that
        breaks the CLI tree renderer and clutters the wire shape.
        Instead, `source` is the state code `ambiguous` and `note` is
        a short one-liner; the CLI / dashboard look up the recovery
        hint separately via `WorkspaceAmbiguousError.recovery_hint()`.

        If you revert the handler change, `source` would be `None` and
        `note` would be the 5-line English block — this test would fail
        on both assertions."""
        fresh = models.User(id='alice', name='alice')
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(
                 users_server.workspaces_core,
                 'resolve_workspace_for_user',
                 side_effect=exceptions.WorkspaceAmbiguousError(
                     accessible=['team-a', 'team-b'])), \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value={'team-a', 'team-b'}), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=False):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        self.assertIsNone(resp['workspace'])
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_AMBIGUOUS)
        # Short, single-line note — no embedded recovery paragraph.
        self.assertNotIn('\n', resp['note'])
        self.assertNotIn('sky workspace use', resp['note'])
        # Accessible is still populated so the CLI can list candidates.
        self.assertEqual(resp['accessible'], ['team-a', 'team-b'])

    def test_ambiguous_with_drift_note_surfaces_drift_in_note(self):
        """Drift case: user had a preferred that lost access AND ended
        up ambiguous. The exception carries a short `note` (the drift
        explanation); the handler must prefer that over the generic
        one-liner so the CLI surfaces the actual cause."""
        fresh = models.User(id='alice',
                            name='alice',
                            preferred_workspace='team-x')
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(
                 users_server.workspaces_core,
                 'resolve_workspace_for_user',
                 side_effect=exceptions.WorkspaceAmbiguousError(
                     accessible=['team-a', 'team-b'],
                     note="preferred 'team-x' not accessible")), \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value={'team-a', 'team-b'}), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=False):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_AMBIGUOUS)
        self.assertEqual(resp['note'], "preferred 'team-x' not accessible")

    def test_no_workspace_access_surfaces_message_verbatim(self):
        """User has zero accessible workspaces. The raise-site message
        already names the user ("User <name> (<id>) has no accessible
        workspaces.") — short enough to fit in the tree row AND more
        informative than a generic stand-in, so the handler surfaces it
        verbatim (same pattern as PERMISSION_DENIED)."""
        fresh = models.User(id='alice', name='alice')
        raise_msg = 'User alice (alice) has no accessible workspaces.'
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(
                 users_server.workspaces_core,
                 'resolve_workspace_for_user',
                 side_effect=exceptions.NoWorkspaceAccessError(raise_msg)), \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value=set()), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=False):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user))
        self.assertIsNone(resp['workspace'])
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_NO_ACCESS)
        self.assertEqual(resp['note'], raise_msg)
        self.assertEqual(resp['accessible'], [])

    def test_permission_denied_surfaces_permission_denied_source(self):
        """Explicit `requested` workspace the user can't access. The
        handler keeps `str(e)` here (unlike the ambiguous path) because
        the exception names the specific workspace + RBAC reason —
        information the structured payload alone does not carry."""
        fresh = models.User(id='alice', name='alice')
        denied_msg = ("User 'alice' does not have permission to "
                      "access workspace 'team-locked'")
        with mock.patch.object(users_server.global_user_state,
                               'get_user',
                               return_value=fresh), \
             mock.patch.object(
                 users_server.workspaces_core,
                 'resolve_workspace_for_user',
                 side_effect=exceptions.PermissionDeniedError(denied_msg)), \
             mock.patch.object(users_server.workspaces_core,
                               'get_accessible_workspace_names',
                               return_value={'team-a', 'team-b'}), \
             mock.patch.object(users_server.skypilot_config,
                               'is_active_workspace_set',
                               return_value=False):
            resp = users_server.get_user_workspace(_fake_request(
                self.auth_user),
                                                   requested='team-locked')
        self.assertIsNone(resp['workspace'])
        self.assertEqual(resp['source'],
                         workspace_constants.WORKSPACE_SOURCE_PERMISSION_DENIED)
        # The note retains the exception message — it names the
        # specific workspace, which the payload doesn't.
        self.assertEqual(resp['note'], denied_msg)


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


# SDK decorator gate ---------------------------------------------------


class TestSetPreferredWorkspaceSdkVersionGate(unittest.TestCase):
    """A new SDK method that calls a new server endpoint MUST be decorated
    with `@versions.minimal_api_version(<API_VERSION>)` so a new client
    talking to an older server gets a clear APINotSupportedError instead
    of a confusing HTTP 404 / connection error on the unknown route.
    See CONTRIBUTING.md guideline for new SDK methods."""

    def test_set_preferred_workspace_raises_when_server_too_old(self):
        from sky import exceptions
        from sky.client import sdk
        from sky.server import versions

        # Pin remote version to one less than the workspace-resolver
        # introducing version. The decorator MUST fire and surface a
        # clean APINotSupportedError before any HTTP attempt.
        too_old = (server_constants.MIN_PREFERRED_WORKSPACE_API_VERSION - 1)
        original = versions.get_remote_api_version
        versions.get_remote_api_version = lambda: too_old  # type: ignore
        try:
            with self.assertRaises(exceptions.APINotSupportedError) as cm:
                sdk.set_preferred_workspace('team-a')
            self.assertIn('set_preferred_workspace', str(cm.exception))
        finally:
            versions.get_remote_api_version = original  # type: ignore
            versions.set_remote_api_version(None)


# client_api_version propagation --------------------------------------


class TestRequestBodyCarriesClientApiVersion(unittest.TestCase):
    """The client API version MUST be available on RequestBody in the
    worker process — the `_remote_api_version` ContextVar set by
    APIVersionMiddleware does not propagate from the FastAPI async event
    loop into worker processes spawned by BurstableExecutor
    (ProcessPoolExecutor). The chosen design: server-side
    `prepare_request_async` reads the ContextVar (set from the
    `X-SkyPilot-API-Version` header) and stamps the body — so the field
    is fully server-driven from the wire header, and neither the Python
    SDK nor the dashboard need their own body-stamping logic.

    This class guards three properties:
      * No client-side stamping happens in `RequestBody.__init__`.
      * The field round-trips through Pydantic JSON serialization.
      * `prepare_request_async` writes the ContextVar value to the body.
    """

    def test_construction_does_not_stamp_field_anywhere(self):
        """`RequestBody.__init__` must NOT populate `client_api_version`
        — server-side `prepare_request_async` is the single point of
        truth for this field. Stamping in `__init__` would either need
        a fragile `is_on_api_server` check (and old clients would get
        the server's API_VERSION on round-trip, silently bypassing the
        gate) or would mis-mark requests that bypass the header path.
        """
        body = payloads.RequestBody()
        self.assertIsNone(body.client_api_version)

    def test_server_side_parse_of_old_client_body_leaves_field_none(self):
        """Back-compat: old clients (pre-this-PR) do not send the
        `X-SkyPilot-API-Version` header for the workspace-resolver
        feature, so prepare_request_async stamps None — which the
        worker-side gate interprets as 'skip the resolver' (legacy
        behavior). Pydantic parsing of the body alone must NOT
        substitute any non-None value.
        """
        old_body_json = (
            '{"env_vars": {}, "entrypoint": "", "entrypoint_command": "",'
            ' "using_remote_api_server": false}')
        body = payloads.RequestBody.model_validate_json(old_body_json)
        self.assertIsNone(body.client_api_version)

    def test_server_side_parse_of_explicit_field_preserves_value(self):
        """If a body arrives with the field already set (e.g. a
        synthetic or test client), Pydantic preserves it. The server
        will then overwrite it from the header in
        `prepare_request_async`, but the parse step itself must not
        clobber explicit values."""
        body_json = (
            '{"env_vars": {}, "entrypoint": "", "entrypoint_command": "",'
            ' "using_remote_api_server": false, "client_api_version": 53}')
        body = payloads.RequestBody.model_validate_json(body_json)
        self.assertEqual(body.client_api_version, 53)

    def test_explicit_override_is_respected(self):
        """Tests and synthetic callers must be able to set the field
        directly via kwargs."""
        body = payloads.RequestBody(client_api_version=42)
        self.assertEqual(body.client_api_version, 42)
        body_none = payloads.RequestBody(client_api_version=None)
        self.assertIsNone(body_none.client_api_version)

    def test_field_survives_pydantic_roundtrip(self):
        """Persisting the body to the request DB goes through
        model_dump_json + model_validate_json. The field MUST be
        present on the rehydrated body — otherwise the executor's
        worker reads None and the resolver never fires."""
        original = payloads.RequestBody(client_api_version=99)
        rehydrated = payloads.RequestBody.model_validate_json(
            original.model_dump_json())
        self.assertEqual(rehydrated.client_api_version, 99)


class TestPrepareRequestAsyncStampsClientApiVersion(
        unittest.IsolatedAsyncioTestCase):
    """`prepare_request_async` is the choke point where every external
    request crosses from the FastAPI dispatch process (where the
    `_remote_api_version` ContextVar set by APIVersionMiddleware is
    valid) into the request DB / worker pool (where it is not). It MUST
    capture the ContextVar value onto the request body so the worker can
    see it.
    """

    async def asyncSetUp(self):
        from sky.server import versions
        from sky.server.requests import executor
        self.executor = executor
        self.versions = versions
        # Reset the ContextVar between tests so leakage from elsewhere
        # in the suite does not contaminate this one.
        versions.set_remote_api_version(None)

    async def asyncTearDown(self):
        self.versions.set_remote_api_version(None)

    async def _prepare_one(self):
        """Drive prepare_request_async with a benign body + mocked DB
        write, return the client_api_version that ended up on the body.
        """
        from sky import models
        from sky.server.requests import request_names
        from sky.server.requests import requests as api_requests

        async def _stub_create_if_not_exists_async(_request):
            return True

        body = payloads.RequestBody()
        # Sanity: construction itself does not populate the field.
        self.assertIsNone(body.client_api_version)

        def _noop():
            return None

        with mock.patch.object(api_requests,
                               'create_if_not_exists_async',
                               side_effect=_stub_create_if_not_exists_async), \
             mock.patch.object(self.executor.global_user_state,
                               'add_or_update_user'):
            await self.executor.prepare_request_async(
                request_id='r-0',
                request_name=request_names.RequestName.CLUSTER_LAUNCH,
                request_body=body,
                func=_noop,
                auth_user=models.User(id='u', name='u'),
            )
        return body.client_api_version

    async def test_stamps_from_contextvar_when_header_present(self):
        """When APIVersionMiddleware has set the ContextVar from a
        header, prepare_request_async writes that value onto the body
        — the value the worker will see."""
        self.versions.set_remote_api_version(57)
        self.assertEqual(await self._prepare_one(), 57)

    async def test_stamps_none_when_header_absent(self):
        """An old client without the version header leaves the
        ContextVar at its default (None). prepare_request_async writes
        None onto the body, which the worker-side gate interprets as
        'old client → skip resolver'."""
        # ContextVar was reset to None in asyncSetUp.
        self.assertIsNone(await self._prepare_one())


# Executor resolver-gate ----------------------------------------------


class TestExecutorResolverGate(unittest.TestCase):
    """Verify _should_apply_workspace_resolver() gates the per-user
    resolver correctly for the three skip-cases (daemon / old client /
    explicit active_workspace) AND fires for the normal case.

    The client API version is now passed in as a parameter (read from
    the RequestBody by the caller in `override_request_env_and_config`)
    instead of being pulled from the `versions.get_remote_api_version()`
    ContextVar, which does not propagate from the FastAPI dispatch
    process into worker processes spawned by BurstableExecutor.
    """

    def setUp(self):
        from sky.server.requests import executor
        self.executor = executor

    def _call(self, is_daemon: bool, api_version, active_workspace_set: bool):
        from sky import skypilot_config
        with mock.patch.object(skypilot_config,
                               'is_active_workspace_set',
                               return_value=active_workspace_set):
            return self.executor._should_apply_workspace_resolver(
                is_daemon, api_version)

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
        """If RequestBody.client_api_version is None (older client that
        constructed the body before the field existed), be conservative
        — don't run the resolver."""
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
