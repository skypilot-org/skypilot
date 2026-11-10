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

_NEW = {'private': True, 'allowed_users': ['alice'], 'gcp': {'project_id': 'q'}}


@pytest.fixture(name='stored')
def stored_fixture(monkeypatch):
    """The stored workspaces; reads see them and the locked update writes them.
    """
    workspaces = copy.deepcopy(_STORED)
    monkeypatch.setattr(core, '_validate_workspace_config',
                        lambda *a, **k: None)
    monkeypatch.setattr(core, '_validate_workspace_config_changes_with_lock',
                        lambda *a, **k: None)
    monkeypatch.setattr(core.workspaces_utils, 'get_workspace_users',
                        lambda config: [])
    monkeypatch.setattr(core.skypilot_config, 'safe_reload_config',
                        lambda: None)
    monkeypatch.setattr(core.skypilot_config,
                        'get_nested',
                        lambda keys, default_value=None: workspaces)

    def run_under_lock(modifier):
        modifier(workspaces)
        return workspaces

    monkeypatch.setattr(core, '_update_workspaces_config', run_under_lock)
    with mock.patch.object(core.permission.permission_service,
                           'update_workspace_policy'):
        yield workspaces


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


def test_expected_empty_config_matches_an_empty_workspace(stored):
    stored['empty'] = {}
    core.update_workspace('empty', {'private': False}, expected_config={})
    assert stored['empty'] == {'private': False}


def test_a_deleted_workspace_conflicts_even_if_it_was_empty(stored):
    # The editor loaded an empty workspace, which was deleted meanwhile.
    with pytest.raises(exceptions.WorkspaceConfigConflictError):
        core.update_workspace('gone', {'private': False}, expected_config={})
    assert 'gone' not in stored


def test_an_implicit_default_workspace_is_not_a_conflict(stored):
    # `default` isn't in the stored config; the dashboard shows it as {}.
    assert 'default' not in stored
    core.update_workspace('default', {'gcp': {
        'project_id': 'd'
    }},
                          expected_config={})
    assert stored['default'] == {'gcp': {'project_id': 'd'}}


def test_update_workspace_is_a_usage_entrypoint():
    # The helper above it must not have taken the decorator.
    assert hasattr(core.update_workspace, '__wrapped__')
    assert not hasattr(core._check_expected_workspace_config, '__wrapped__')  # pylint: disable=protected-access


def test_conflict_is_reported_before_resource_validation(stored, monkeypatch):
    # Against the newer config the draft looks like a change validation would
    # reject; the outdated snapshot must be what gets reported.
    del stored

    def reject(*args, **kwargs):
        del args, kwargs
        raise ValueError('active resources')

    monkeypatch.setattr(core, '_validate_workspace_config_changes_with_lock',
                        reject)
    outdated = {'private': True, 'allowed_users': ['alice']}
    with pytest.raises(exceptions.WorkspaceConfigConflictError):
        core.update_workspace('dev', _NEW, expected_config=outdated)


def test_a_stale_process_config_is_refreshed_before_the_early_check(
        stored, monkeypatch):
    # This process still holds the config from before someone else's write;
    # the draft is based on the newer one, so it must not conflict.
    newer = copy.deepcopy(stored['dev'])
    stale = {'dev': dict(newer, allowed_users=['alice'])}
    view = {'workspaces': stale}
    monkeypatch.setattr(core.skypilot_config,
                        'get_nested',
                        lambda keys, default_value=None: view['workspaces'])
    monkeypatch.setattr(core.skypilot_config, 'safe_reload_config',
                        lambda: view.update(workspaces=stored))
    core.update_workspace('dev', _NEW, expected_config=newer)
    assert stored['dev'] == _NEW
