"""GET /ssh_node_pools hides credentials from callers who cannot manage pools.

The stored pool config carries each host's sudo password and SSH key path.
Admins are the only role allowed to change the pools, so they get the full
config (the dashboard edit form round-trips it); the user and viewer roles get
it with those fields removed.
"""
from unittest import mock

import fastapi
import pytest

from sky import models
from sky.ssh_node_pools import constants
from sky.ssh_node_pools import core
from sky.ssh_node_pools import server as pools_server

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
}


def _request(user_id):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.auth_user = (models.User(id=user_id, name=user_id)
                               if user_id is not None else None)
    return request


def _get(user_id, roles):
    with mock.patch.object(core, 'get_all_pools', return_value=_POOLS), \
         mock.patch('sky.users.permission.permission_service') as service:
        service.get_user_roles.return_value = roles
        return pools_server.get_ssh_node_pools(_request(user_id))


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
    assert _secrets(_get('root', ['admin'])) == _ALL_SECRETS


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
