"""Tests for service accounts in workspace ``allowed_users``.

Service accounts are regular ``User`` rows whose id starts with ``sa-``
(see ``models.User.is_service_account``) and whose ``name`` is the token
name. Because of that they can be listed in a private workspace's
``allowed_users`` just like a human user -- either by the token name or
by the ``sa-...`` id -- and the resolver maps them to their user id so
the casbin workspace policy grants them access.

IMPORTANT (the gotcha these tests pin down): a freshly-created service
account is assigned the *default* role, which is ``admin`` unless the
operator overrides ``rbac.default_role``. Admins bypass the workspace
permission check entirely, so listing an admin service account in
``allowed_users`` has no scoping effect -- it can already reach every
workspace. To actually scope a service account to specific workspaces it
must be assigned the ``user`` role first. The end-to-end tests below
cover both the user-role (scoped) and admin (bypass) cases.
"""
# pylint: disable=protected-access,redefined-outer-name
import os

import casbin
import pytest
import sqlalchemy
import sqlalchemy_adapter

from sky import models
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.users import resolver as user_resolver
from sky.workspaces import utils as workspaces_utils

# A regular (human) user and a service account, as they would appear in
# the users table.
_HUMAN = models.User(id='abcd1234', name='alice@example.com', user_type='basic')
_SA = models.User(id='sa-deadbeef00112233',
                  name='my-service-account',
                  user_type=models.UserType.SA.value)


def _resolver():
    return user_resolver.UserResolver(all_users=[_HUMAN, _SA],
                                      admin_user_ids=[])


class TestServiceAccountResolution:
    """The resolver maps SA ``allowed_users`` entries to the SA user id."""

    def test_resolve_by_token_name(self):
        cfg = {
            'private': True,
            'allowed_users': ['my-service-account', 'alice@example.com'],
        }
        assert set(_resolver().resolve_workspace_users(cfg)) == {
            'sa-deadbeef00112233', 'abcd1234'
        }

    def test_resolve_by_sa_id(self):
        cfg = {'private': True, 'allowed_users': ['sa-deadbeef00112233']}
        assert _resolver().resolve_workspace_users(cfg) == [
            'sa-deadbeef00112233'
        ]

    def test_public_workspace_ignores_allowed_users(self):
        # Public workspaces are open to everyone, so the SA entry is moot.
        cfg = {'private': False, 'allowed_users': ['my-service-account']}
        assert _resolver().resolve_workspace_users(cfg) == ['*']

    def test_get_workspace_users_helper_resolves_sa(self):
        # The helper used by create/update_workspace to compute the casbin
        # policy must include the SA user id.
        cfg = {'private': True, 'allowed_users': ['my-service-account']}
        users = workspaces_utils.get_workspace_users(cfg, resolver=_resolver())
        assert users == ['sa-deadbeef00112233']

    def test_unknown_sa_entry_is_dropped(self):
        # An SA that does not exist yet (e.g. token not created) is dropped
        # with a warning rather than granting access -- same as any unknown
        # user.
        cfg = {'private': True, 'allowed_users': ['sa-does-not-exist']}
        assert _resolver().resolve_workspace_users(cfg) == []


@pytest.fixture
def permission_service(tmp_path, monkeypatch):
    """A ``PermissionService`` backed by a real casbin enforcer on sqlite.

    Mirrors the setup in ``test_permission_concurrency`` but additionally
    forces the server-side code path (so the workspace check actually
    enforces instead of short-circuiting to ``True``) and isolates the
    DB-backed permission cache so results aren't cached across assertions.
    """
    # Reset the global enforcer singleton; monkeypatch restores the
    # original value at teardown even if setup below raises.
    monkeypatch.setattr(permission, '_enforcer_instance', None)
    # check_workspace_permission / get_accessible_workspace_names only
    # enforce when running on the API server.
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, '1')
    # Bypass the DB-backed permission cache: always miss, no-op writes, so
    # each assertion re-computes against the live policy.
    monkeypatch.setattr(permission.kv_cache, 'get_cache_entry',
                        lambda *a, **k: None)
    monkeypatch.setattr(permission.kv_cache, 'add_or_update_cache_entry',
                        lambda *a, **k: None)
    monkeypatch.setattr(permission.kv_cache, 'delete_cache_entries_by_prefix',
                        lambda *a, **k: None)
    monkeypatch.setattr(permission.kv_cache,
                        'delete_cache_entries_by_prefix_suffix',
                        lambda *a, **k: None)

    db_path = str(tmp_path / 'test_casbin.db')
    engine = sqlalchemy.create_engine(f'sqlite:///{db_path}',
                                      connect_args={'check_same_thread': False},
                                      poolclass=sqlalchemy.pool.StaticPool)
    sqlalchemy_adapter.Base.metadata.create_all(engine)
    adapter = sqlalchemy_adapter.Adapter(engine,
                                         db_class=sqlalchemy_adapter.CasbinRule)
    model_path = os.path.join(os.path.dirname(permission.__file__),
                              'model.conf')
    enforcer = casbin.SyncedEnforcer(model_path, adapter)

    service = permission.PermissionService()
    service.enforcer = enforcer
    monkeypatch.setattr(permission, '_enforcer_instance', service)
    yield service


class TestServiceAccountWorkspaceScoping:
    """End-to-end: which workspaces a service account can actually reach.

    Exercises the casbin policy the same way ``create_workspace`` /
    ``update_workspace`` do: ``allowed_users`` is resolved to user ids and
    written via ``add_workspace_policy``.
    """

    def test_user_role_sa_is_scoped_to_allowed_workspaces(
            self, permission_service):
        sa_id = _SA.id
        # Grant the 'user' role -- the operator-set role that makes
        # allowed_users actually constrain the SA.
        permission_service.update_role(sa_id, rbac.RoleName.USER.value)

        # private-ws-1 lists the SA in allowed_users; private-ws-2 does not.
        permission_service.add_workspace_policy('private-ws-1', [sa_id])
        permission_service.add_workspace_policy('private-ws-2', ['abcd1234'])

        assert permission_service.check_workspace_permission(
            sa_id, 'private-ws-1') is True
        assert permission_service.check_workspace_permission(
            sa_id, 'private-ws-2') is False

        # Batch path used to filter clusters/jobs agrees.
        accessible = permission_service.get_accessible_workspace_names(
            sa_id, {'private-ws-1', 'private-ws-2'})
        assert accessible == {'private-ws-1'}

    def test_admin_role_sa_bypasses_allowed_users(self, permission_service):
        # Reproduces the default-role gotcha: an admin service account
        # reaches every workspace regardless of allowed_users, so listing
        # it has no scoping effect.
        sa_id = _SA.id
        permission_service.update_role(sa_id, rbac.RoleName.ADMIN.value)

        # Only put the SA in private-ws-1's allowed_users.
        permission_service.add_workspace_policy('private-ws-1', [sa_id])
        permission_service.add_workspace_policy('private-ws-2', ['abcd1234'])

        assert permission_service.check_workspace_permission(
            sa_id, 'private-ws-1') is True
        # Not in allowed_users, but admin -> still allowed.
        assert permission_service.check_workspace_permission(
            sa_id, 'private-ws-2') is True

    def test_public_workspace_accessible_to_user_role_sa(
            self, permission_service):
        # Public workspaces grant access via the '*' wildcard policy.
        sa_id = _SA.id
        permission_service.update_role(sa_id, rbac.RoleName.USER.value)
        permission_service.add_workspace_policy('public-ws', ['*'])
        assert permission_service.check_workspace_permission(
            sa_id, 'public-ws') is True
