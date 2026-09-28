"""update_workspace's optional compare-and-swap on expected_config."""

import copy
from unittest import mock

import pytest

from sky import exceptions
from sky.workspaces import core

_STORED = {
    'dev': {
        'private': True,
        'allowed_users': ['alice', 'bob'],
        'gcp': {
            'project_id': 'p'
        },
    },
}


@pytest.fixture(name='stored')
def stored_fixture(monkeypatch):
    """The workspaces as held under the config lock; updates write into it."""
    workspaces = copy.deepcopy(_STORED)
    monkeypatch.setattr(core, '_validate_workspace_config',
                        lambda *a, **k: None)
    monkeypatch.setattr(core, '_validate_workspace_config_changes_with_lock',
                        lambda *a, **k: None)
    monkeypatch.setattr(core.workspaces_utils, 'get_workspace_users',
                        lambda config: [])

    def run_under_lock(modifier):
        modifier(workspaces)
        return workspaces

    monkeypatch.setattr(core, '_update_workspaces_config', run_under_lock)
    with mock.patch.object(core.permission.permission_service,
                           'update_workspace_policy'):
        yield workspaces


_NEW = {'private': True, 'allowed_users': ['alice'], 'gcp': {'project_id': 'q'}}


def test_without_expected_config_the_update_is_unconditional(stored):
    core.update_workspace('dev', _NEW)
    assert stored['dev'] == _NEW


def test_matching_expected_config_writes(stored):
    # Key order and dict identity don't matter, only content.
    expected = {
        'gcp': {
            'project_id': 'p'
        },
        'allowed_users': ['alice', 'bob'],
        'private': True,
    }
    core.update_workspace('dev', _NEW, expected_config=expected)
    assert stored['dev'] == _NEW


def test_outdated_expected_config_raises_and_writes_nothing(stored):
    outdated = {
        'private': True,
        'allowed_users': ['alice'],
        'gcp': {
            'project_id': 'p'
        }
    }
    with pytest.raises(exceptions.WorkspaceConfigConflictError):
        core.update_workspace('dev', _NEW, expected_config=outdated)
    assert stored['dev'] == _STORED['dev']


def test_expected_empty_config_matches_a_workspace_without_one(stored):
    core.update_workspace('fresh', {'private': False}, expected_config={})
    assert stored['fresh'] == {'private': False}
