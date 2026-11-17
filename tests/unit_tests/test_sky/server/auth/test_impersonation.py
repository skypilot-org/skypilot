"""Who may act as whom through the impersonation header."""

import unittest.mock as mock

import pytest

from sky import models
from sky.server.auth import impersonation
from sky.skylet import constants
from sky.users import rbac

ADMIN = rbac.RoleName.ADMIN.value
USER = rbac.RoleName.USER.value
VIEWER = rbac.RoleName.VIEWER.value

_ADMIN_SA = models.User(id='sa-admin', name='platform')
_USER_SA = models.User(id='sa-user', name='pipelines')
_ALICE = models.User(id='a1b2c3d4', name='alice@example.com')
_BOSS = models.User(id='b0b0b0b0', name='boss@example.com')
_VIEWER_HUMAN = models.User(id='c1c1c1c1', name='carol@example.com')
_USERS = {
    u.id: u for u in [
        _ALICE,
        _BOSS,
        _VIEWER_HUMAN,
        models.User(id='sa-other', name='other-sa'),
        models.User(id=constants.SKYPILOT_SYSTEM_USER_ID,
                    name='skypilot-system'),
    ]
}
_ROLES = {
    _ADMIN_SA.id: [ADMIN],
    _USER_SA.id: [USER],
    _ALICE.id: [USER],
    _BOSS.id: [ADMIN],
    _VIEWER_HUMAN.id: [VIEWER],
}


def _users_by_name(name):
    if name == 'dup':
        return [_ALICE, models.User(id='e5f6a7b8', name='dup')]
    return [u for u in _USERS.values() if u.name == name]


@pytest.fixture
def env():
    """Policy dependencies, with impersonation enabled and nothing blocked."""
    state = {'enabled': True, 'enabled_after_reload': None}

    def get_nested(keys, default_value=None):
        if tuple(keys) == ('rbac', 'allow_impersonation'):
            return state['enabled']
        return default_value

    def reload():
        if state['enabled_after_reload'] is not None:
            state['enabled'] = state['enabled_after_reload']

    with mock.patch('sky.users.permission.permission_service') as perm, \
            mock.patch('sky.global_user_state.get_user',
                       side_effect=_USERS.get), \
            mock.patch('sky.global_user_state.get_user_by_name',
                       side_effect=_users_by_name), \
            mock.patch('sky.skypilot_config.get_nested',
                       side_effect=get_nested), \
            mock.patch('sky.skypilot_config.safe_reload_config',
                       side_effect=reload) as reload_mock, \
            mock.patch.object(impersonation, '_policy', None):
        perm.roles_in_memory.side_effect = lambda uid: _ROLES.get(uid, [])
        perm.get_user_roles.side_effect = lambda uid: _ROLES.get(uid, [])
        perm.check_endpoint_permission.return_value = False
        yield mock.Mock(perm=perm, state=state, reload=reload_mock)


def _check(caller, target, path='/launch', method='POST'):
    return impersonation.check(caller, target, path, method)


@pytest.mark.parametrize('target', [_ALICE.name, _ALICE.id, _BOSS.name])
def test_admin_service_account_acts_as_user_when_enabled(env, target):
    del env
    assert _check(_ADMIN_SA, target).id in (_ALICE.id, _BOSS.id)


def test_disabled_by_default(env):
    env.state['enabled'] = False
    with pytest.raises(impersonation.Refused, match='not enabled'):
        _check(_ADMIN_SA, _ALICE.name)


def test_setting_enabled_on_another_worker_counts(env):
    env.state.update(enabled=False, enabled_after_reload=True)
    assert _check(_ADMIN_SA, _ALICE.name).id == _ALICE.id
    env.reload.assert_called_once()


def test_config_is_read_from_memory_when_enabled(env):
    _check(_ADMIN_SA, _ALICE.name)
    env.reload.assert_not_called()


@pytest.mark.parametrize('caller,reason', [
    (_ALICE, 'requires a service account token'),
    (_USER_SA, 'Only service accounts with the admin role'),
])
def test_default_policy_allows_admin_service_accounts_only(env, caller, reason):
    del env
    with pytest.raises(impersonation.Refused, match=reason):
        _check(caller, _VIEWER_HUMAN.name)


@pytest.mark.parametrize('caller', [_ALICE, _USER_SA])
def test_only_allowed_callers_reload_the_config(env, caller):
    env.state['enabled'] = False
    with pytest.raises(impersonation.Refused):
        _check(caller, _VIEWER_HUMAN.name)
    env.reload.assert_not_called()


def test_only_service_accounts_impersonate_whatever_the_policy(env):
    del env
    impersonation.set_policy(lambda caller, user: None)
    with pytest.raises(impersonation.Refused,
                       match='requires a service account token'):
        _check(_BOSS, _ALICE.name)
    with pytest.raises(impersonation.Refused,
                       match='requires a service account token'):
        impersonation.precheck(_BOSS, '/launch', 'POST')


@pytest.mark.parametrize('path', [
    '/users/update',
    '/users/create',
    '/users/service-account-tokens',
    '/users/service-account-tokens/rotate',
    '/users/service-account-tokens/delete',
])
def test_users_and_tokens_cannot_be_changed(env, path):
    del env
    impersonation.set_policy(lambda caller, user: None)
    with pytest.raises(impersonation.Refused, match='cannot change users'):
        _check(_ADMIN_SA, _ALICE.name, path, 'POST')


@pytest.mark.parametrize('path,method', [
    ('/users/role', 'GET'),
    ('/users/service-account-tokens', 'GET'),
    ('/users_and_more', 'POST'),
])
def test_other_requests_are_not_refused_by_path(env, path, method):
    del env
    impersonation.set_policy(lambda caller, user: None)
    assert _check(_ADMIN_SA, _ALICE.name, path, method).id == _ALICE.id


def test_admin_role_granted_on_another_worker_is_honored(env):
    env.perm.roles_in_memory.side_effect = lambda uid: []
    assert _check(_ADMIN_SA, _ALICE.name).id == _ALICE.id


def test_roles_are_read_from_memory_on_the_hot_path(env):
    _check(_ADMIN_SA, _ALICE.name)
    env.perm.get_user_roles.assert_not_called()


@pytest.mark.parametrize('target,reason', [
    ('', 'must not be empty'),
    ('nobody', 'not found'),
    ('dup', 'matches 2 users'),
    ('sa-other', 'Service accounts cannot'),
    (constants.SKYPILOT_SYSTEM_USER_ID, 'system users cannot'),
])
def test_targets_refused_whatever_the_policy(env, target, reason):
    del env
    impersonation.set_policy(lambda caller, user: None)
    with pytest.raises(impersonation.Refused, match=reason):
        _check(_ADMIN_SA, target)


def test_registered_policy_replaces_the_default(env):
    env.state['enabled'] = False
    seen = []

    def policy(caller, user):
        seen.append((caller.id, user.id))
        return None if caller.id == _USER_SA.id else 'no grant'

    impersonation.set_policy(policy)
    assert _check(_USER_SA, _ALICE.name).id == _ALICE.id
    with pytest.raises(impersonation.Refused, match='no grant'):
        _check(_ADMIN_SA, _ALICE.name)
    assert seen == [(_USER_SA.id, _ALICE.id), (_ADMIN_SA.id, _ALICE.id)]


@pytest.mark.parametrize('caller,target,allowed', [
    (_USER_SA, _ALICE, True),
    (_USER_SA, _VIEWER_HUMAN, True),
    (_USER_SA, _BOSS, False),
    (_ADMIN_SA, _BOSS, True),
])
def test_target_role_may_not_outrank_the_caller(env, caller, target, allowed):
    del env
    impersonation.set_policy(lambda caller, user: None)
    if allowed:
        assert _check(caller, target.name).id == target.id
    else:
        with pytest.raises(impersonation.Refused, match='outranks'):
            _check(caller, target.name)


def test_caller_role_must_allow_the_endpoint(env):
    impersonation.set_policy(lambda caller, user: None)
    env.perm.check_endpoint_permission.side_effect = (
        lambda uid, path, method: uid == _USER_SA.id)
    with pytest.raises(impersonation.Refused, match='does not allow POST'):
        _check(_USER_SA, _ALICE.name)
    # Request management and dashboard paths are left to their own checks,
    # as the RBAC middleware does.
    assert _check(_USER_SA, _ALICE.name, '/api/get', 'GET').id == _ALICE.id
    env.perm.check_endpoint_permission.assert_called_once_with(
        _USER_SA.id, '/launch', 'POST')
