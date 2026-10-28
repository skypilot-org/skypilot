"""GET /ssh_node_pools hides credentials from callers who cannot manage pools.

The stored pool config carries each host's sudo password. Admins are the only
role allowed to change the pools, so they get the full config (the dashboard
edit form round-trips it); the user and viewer roles get it with the password
fields removed.
"""
from unittest import mock

import fastapi
import pytest

from sky import models
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
            'identity_file': '/root/.sky/ssh_keys/prod-0',
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


def _passwords(pools):
    found = []
    for config in pools.values():
        if not isinstance(config, dict):
            continue
        if 'password' in config:
            found.append(config['password'])
        for host in config.get('hosts', []):
            if isinstance(host, dict) and 'password' in host:
                found.append(host['password'])
    return found


def test_redact_pool_secrets_strips_both_levels_and_copies():
    redacted = core.redact_pool_secrets(_POOLS)
    assert _passwords(redacted) == []
    # Everything else is intact, including non-dict entries.
    assert redacted['prod']['user'] == 'ubuntu'
    assert redacted['prod']['identity_file'] == '/root/.sky/ssh_keys/prod-0'
    assert redacted['prod']['hosts'][0]['ip'] == '10.0.0.1'
    assert redacted['prod']['hosts'][1] == 'bare-host-string'
    assert redacted['weird'] == 'not-a-dict'
    # The input was not modified.
    assert _passwords(_POOLS) == ['pool-sudo-secret', 'host-sudo-secret']


@pytest.mark.parametrize('roles', [['user'], ['viewer'], []])
def test_non_admin_read_has_no_passwords(roles):
    assert _passwords(_get('mallory', roles)) == []


def test_admin_read_keeps_passwords():
    pools = _get('root', ['admin'])
    assert _passwords(pools) == ['pool-sudo-secret', 'host-sudo-secret']


def test_unauthenticated_read_keeps_passwords():
    # No auth configured: no role to distinguish, same as the RBAC middleware.
    pools = _get(None, [])
    assert _passwords(pools) == ['pool-sudo-secret', 'host-sudo-secret']
