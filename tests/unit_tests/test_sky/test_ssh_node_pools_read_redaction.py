"""SSH Node Pool reads are limited to what the caller may see.

The stored pool config carries each host's sudo password and SSH key path.
Admins are the only role allowed to change the pools, so they get the full
config (the dashboard edit form round-trips it); the user and viewer roles get
only the pools their workspaces allow, with those fields removed.
"""
from unittest import mock

import fastapi
import pytest

from sky import models
from sky import skypilot_config
from sky.ssh_node_pools import constants
from sky.ssh_node_pools import core
from sky.ssh_node_pools import server as pools_server
from sky.workspaces import visibility

_POOLS = {
    'prod': {
        'user': 'ubuntu',
        'identity_file': '/root/.sky/ssh_keys/prod-0',
        'password': 'pool-sudo-secret',
        'hosts': [{
            'ip': '10.0.0.1',
            'user': 'ubuntu',
            'identity_file': '/root/.sky/ssh_keys/prod-host-0',
            'password': 'host-sudo-secret',
        }, 'bare-host-string'],
    },
    'weird': 'not-a-dict',
    'other-team': {
        'hosts': ['10.9.9.9']
    },
}


def _request(user_id):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.auth_user = (models.User(id=user_id, name=user_id)
                               if user_id is not None else None)
    return request


def _scoped(roles, visible_pools):
    """Patches the scope lookup: the caller's workspaces allow
    ``visible_pools``."""
    service = mock.patch('sky.users.permission.permission_service')
    patches = [
        mock.patch.object(visibility.server_common,
                          'refresh_workspace_state_for_sync_handler'),
        mock.patch.object(visibility.workspaces_core,
                          'workspaces_for_user',
                          return_value={'team-a': {}}),
        mock.patch.object(visibility,
                          'visible_for_workspaces',
                          side_effect=lambda _, cloud:
                          ({f'ssh-{p}' for p in visible_pools}
                           if cloud == 'ssh' else set())),
    ]
    for p in patches:
        p.start()
    mocked_service = service.start()
    mocked_service.get_user_roles.return_value = roles

    def stop():
        service.stop()
        for p in patches:
            p.stop()

    return stop


def _get(user_id, roles, visible_pools=('prod',)):
    stop = _scoped(roles, visible_pools)
    try:
        with mock.patch.object(core, 'get_all_pools', return_value=_POOLS):
            return pools_server.get_ssh_node_pools(_request(user_id))
    finally:
        stop()


def _secrets(pools):
    found = []
    for config in pools.values():
        if not isinstance(config, dict):
            continue
        entries = [config] + [
            h for h in config.get('hosts', []) if isinstance(h, dict)
        ]
        for entry in entries:
            found += [
                entry[k] for k in ('password', 'identity_file') if k in entry
            ]
    return found


_ALL_SECRETS = [
    'pool-sudo-secret', '/root/.sky/ssh_keys/prod-0', 'host-sudo-secret',
    '/root/.sky/ssh_keys/prod-host-0'
]


def test_redact_pool_secrets_strips_both_levels_and_copies():
    redacted = core.redact_pool_secrets(_POOLS)
    assert _secrets(redacted) == []
    # Everything else is intact, including non-dict entries.
    assert redacted['prod']['user'] == 'ubuntu'
    assert redacted['prod']['hosts'][0]['ip'] == '10.0.0.1'
    assert redacted['prod']['hosts'][0]['user'] == 'ubuntu'
    assert redacted['prod']['hosts'][1] == 'bare-host-string'
    assert redacted['weird'] == 'not-a-dict'
    # The input was not modified.
    assert _secrets(_POOLS) == _ALL_SECRETS


@pytest.mark.parametrize('roles', [['user'], ['viewer'], []])
def test_non_admin_read_has_no_secrets(roles):
    assert _secrets(_get('mallory', roles)) == []


def test_admin_read_keeps_secrets():
    pools = _get('root', ['admin'], visible_pools=())
    assert _secrets(pools) == _ALL_SECRETS
    assert set(pools) == set(_POOLS)


@pytest.mark.parametrize('roles', [['user'], ['viewer']])
def test_non_admin_read_only_sees_allowed_pools(roles):
    assert set(_get('mallory', roles)) == {'prod'}
    assert _get('mallory', roles, visible_pools=()) == {}


def test_admin_with_extra_role_is_scoped():
    # Same rule as the rest of the infra reads: only a pure admin is unscoped.
    assert set(_get('root', ['admin', 'user'])) == {'prod'}


def test_unauthenticated_read_keeps_secrets():
    # No auth configured: no role to distinguish, same as the RBAC middleware.
    assert _secrets(_get(None, [])) == _ALL_SECRETS


def test_update_pools_leaves_other_pools_alone(tmp_path, monkeypatch):
    # The dashboard saves one pool at a time and relies on this per-pool merge,
    # so a redacted copy of the other pools is never written back.
    monkeypatch.setattr(constants, 'DEFAULT_SSH_NODE_POOLS_PATH',
                        str(tmp_path / 'pools.yaml'))
    monkeypatch.setattr(constants, 'NODE_POOLS_KEY_DIR', str(tmp_path / 'keys'))
    core.update_pools({'prod': _POOLS['prod'], 'dev': {'hosts': ['10.0.0.2']}})
    core.update_pools({'dev': {'hosts': ['10.0.0.3'], 'user': 'ubuntu'}})
    pools = core.get_all_pools()
    assert pools['prod'] == _POOLS['prod']
    assert pools['dev'] == {'hosts': ['10.0.0.3'], 'user': 'ubuntu'}


_WORKSPACES = {
    'default': {},
    'team-b': {
        'ssh': {
            'allowed_node_pools': ['prod']
        }
    },
}


def _status(user_id, roles, visible_pools, pool='prod'):
    """Calls the status route; returns (response, workspace the check ran in).
    """
    real_get_nested = skypilot_config.get_nested

    def get_nested(keys, default_value=None, *args, **kwargs):
        if tuple(keys) == ('workspaces',):
            return _WORKSPACES
        return real_get_nested(keys, default_value, *args, **kwargs)

    seen = []

    def ssh_status(context):
        seen.append(skypilot_config.get_active_workspace())
        return True, 'SSH Node Pool is set up.'

    stop = _scoped(roles, visible_pools)
    try:
        with mock.patch.object(skypilot_config, 'get_nested',
                               side_effect=get_nested), \
             mock.patch.object(visibility, '_workspace_targets',
                               side_effect=lambda ws, _: ({'ssh-prod'}
                                                          if ws == 'team-b'
                                                          else set())), \
             mock.patch.object(core, 'ssh_status', side_effect=ssh_status):
            response = pools_server.get_ssh_node_pool_status(
                _request(user_id), pool)
    finally:
        stop()
    return response, seen


def test_status_of_hidden_pool_is_404():
    with pytest.raises(fastapi.HTTPException) as e:
        _status('mallory', ['user'], visible_pools=(), pool='other-team')
    assert e.value.status_code == 404


def test_status_runs_in_a_workspace_that_allows_the_pool():
    # The server's default workspace does not allow `prod`; checking there
    # would report it as not set up.
    response, seen = _status('mallory', ['user'], visible_pools=('prod',))
    assert response['status'] == 'Ready'
    assert seen == ['team-b']
    response, seen = _status('root', ['admin'], visible_pools=())
    assert seen == ['team-b']


def test_status_of_pool_no_workspace_allows_keeps_default():
    _, seen = _status('root', ['admin'], visible_pools=(), pool='other-team')
    assert seen == ['default']
