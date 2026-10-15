"""Renaming a workspace: the key is the immutable id, `name` is the label."""

import contextlib
import copy
from unittest import mock

import pytest

from sky import exceptions
from sky import skypilot_config
from sky.workspaces import core


@pytest.fixture(name='store')
def store_fixture(monkeypatch):
    """An in-memory config store behind the real workspace write paths.

    Every workspace reports active resources, so any change that is not
    exempt from the active-resource guard fails with 'has active resources'.
    """
    store = {'workspaces': {}}

    def get_nested(keys, default_value=None, **kwargs):
        del kwargs
        value = store
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default_value
            value = value[key]
        return copy.deepcopy(value)

    def save(config):
        store.clear()
        store.update(copy.deepcopy(dict(config)))

    def active_resources(workspaces):
        if workspaces:
            raise ValueError(f'{workspaces} has active resources')

    monkeypatch.setattr(skypilot_config, 'get_nested', get_nested)
    monkeypatch.setattr(skypilot_config, 'to_dict',
                        lambda: copy.deepcopy(store))
    monkeypatch.setattr(skypilot_config, 'reload_config', lambda: None)
    monkeypatch.setattr(skypilot_config, 'safe_reload_config', lambda: None)
    monkeypatch.setattr(skypilot_config, 'update_api_server_config_no_lock',
                        save)
    monkeypatch.setattr(skypilot_config, 'get_skypilot_config_lock',
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(core.locks, 'get_lock',
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(core.resource_checker,
                        'check_no_active_resources_for_workspaces',
                        active_resources)
    monkeypatch.setattr(core.workspaces_utils, 'get_workspace_users',
                        lambda *a, **k: [])
    monkeypatch.setattr(core.user_resolver, 'UserResolver',
                        lambda: mock.Mock(admin_user_ids=[]))
    monkeypatch.setattr(core.permission, 'permission_service', mock.Mock())
    monkeypatch.setattr(core.sky_check, 'check', mock.Mock())
    return store


def _workspaces(store):
    return store['workspaces']


def test_rename_with_active_resources_succeeds(store):
    _workspaces(store)['team-a'] = {'gcp': {'project_id': 'p'}}

    core.update_workspace('team-a', {
        'gcp': {
            'project_id': 'p'
        },
        'name': 'proj-a'
    })

    assert _workspaces(store)['team-a']['name'] == 'proj-a'
    # A rename touches no cloud config, so it skips the slow re-check.
    core.sky_check.check.assert_not_called()


def test_rename_through_whole_config_update_succeeds(store):
    _workspaces(store)['team-a'] = {'gcp': {'project_id': 'p'}}
    new_config = copy.deepcopy(store)
    new_config['workspaces']['team-a']['name'] = 'proj-a'

    core.update_config(new_config)

    assert _workspaces(store)['team-a']['name'] == 'proj-a'


def test_rename_with_infra_change_is_still_guarded(store):
    _workspaces(store)['team-a'] = {'gcp': {'project_id': 'p'}}

    with pytest.raises(ValueError, match='active resources'):
        core.update_workspace('team-a', {
            'gcp': {
                'project_id': 'q'
            },
            'name': 'proj-a'
        })
    assert 'name' not in _workspaces(store)['team-a']


def test_rename_with_additive_allowed_contexts_succeeds(store):
    _workspaces(store)['team-a'] = {
        'kubernetes': {
            'allowed_contexts': ['ctx-a']
        }
    }

    core.update_workspace('team-a', {
        'kubernetes': {
            'allowed_contexts': ['ctx-a', 'ctx-b']
        },
        'name': 'proj-a'
    })

    assert _workspaces(store)['team-a']['name'] == 'proj-a'


@pytest.mark.parametrize('bad_name', ['Team-A', 'a b', 'a' * 64])
def test_invalid_name_is_rejected(store, bad_name):
    _workspaces(store)['team-a'] = {}

    with pytest.raises(exceptions.InvalidWorkspaceNameError):
        core.update_workspace('team-a', {'name': bad_name})


def test_name_must_be_unique_among_current_names(store):
    _workspaces(store).update({'team-a': {}, 'team-b': {}})

    # 'team-b' is the current name of workspace 'team-b'.
    with pytest.raises(ValueError, match="'team-b' already exists"):
        core.update_workspace('team-a', {'name': 'team-b'})
    # 'default' is always taken, stored or not.
    with pytest.raises(ValueError, match="'default' already exists"):
        core.update_workspace('team-a', {'name': 'default'})
    # A workspace may be named after its own id.
    core.update_workspace('team-a', {'name': 'team-a'})


def test_legacy_workspaces_original_name_stays_taken(store):
    _workspaces(store).update({'team-a': {}, 'team-b': {}})
    core.update_workspace('team-b', {'name': 'archive'})

    # 'team-b' is still workspace 'archive''s id, so it cannot name another.
    with pytest.raises(ValueError, match="is the id of workspace 'archive'"):
        core.update_workspace('team-a', {'name': 'team-b'})
    with pytest.raises(ValueError, match="is the id of workspace 'archive'"):
        core.create_workspace('team-b', {})
    # The workspace itself may take its original name back.
    core.update_workspace('team-b', {'name': 'team-b'})


def test_generated_id_workspaces_old_name_is_reusable(store):
    (old_id,) = core.create_workspace('proj-x', {}).keys() - {'default'}
    core.update_workspace(old_id, {'name': 'proj-y'})

    result = core.create_workspace('proj-x', {})

    (new_id,) = set(result) - {old_id}
    assert result[new_id]['name'] == 'proj-x'


def test_new_raw_key_may_not_be_another_workspaces_name(store):
    _workspaces(store)['ws-0123456789ab'] = {'name': 'proj-x'}
    new_config = copy.deepcopy(store)
    new_config['workspaces']['proj-x'] = {}

    with pytest.raises(ValueError, match="already the name of workspace"):
        core.update_config(new_config)


def test_minted_id_never_equals_a_name(monkeypatch):
    hexes = iter(['0123456789ab', 'aaaaaaaaaaaa'])
    monkeypatch.setattr(core.secrets, 'token_hex', lambda n: next(hexes))
    workspaces = {'team-a': {'name': 'ws-0123456789ab'}}

    assert core._new_workspace_id(workspaces) == 'ws-aaaaaaaaaaaa'


def test_existing_duplicate_does_not_block_unrelated_writes(store):
    # A hand-edited config can hold a duplicate; only new collisions fail.
    _workspaces(store).update({'a': {'name': 'x'}, 'b': {'name': 'x'}, 'c': {}})

    core.update_workspace('c', {'private': False})
    with pytest.raises(ValueError, match="'x' already exists"):
        core.update_workspace('c', {'name': 'x'})


def test_default_cannot_be_renamed(store):
    with pytest.raises(ValueError, match='cannot be renamed'):
        core.update_workspace('default', {'name': 'main'})
    new_config = copy.deepcopy(store)
    new_config['workspaces']['default'] = {'name': 'main'}
    with pytest.raises(ValueError, match='cannot be renamed'):
        core.update_config(new_config)
    # Naming it after itself is a no-op, not a rename.
    core.update_workspace('default', {'name': 'default'})


def test_default_name_ignores_a_hand_edited_value():
    workspaces = {'default': {'name': 'main'}, 'team-a': {'name': 'proj-a'}}
    assert skypilot_config.get_workspace_name('default',
                                              workspaces) == ('default')
    assert skypilot_config.get_workspace_name('team-a', workspaces) == 'proj-a'
    assert skypilot_config.get_workspace_name('team-b', workspaces) == 'team-b'


def test_create_mints_an_id_and_stores_the_name(store):
    result = core.create_workspace('proj-x', {'private': False})

    (new_id,) = result.keys()
    assert new_id.startswith('ws-') and len(new_id) == len('ws-') + 12
    assert result[new_id] == {'private': False, 'name': 'proj-x'}
    with pytest.raises(ValueError, match="'proj-x' already exists"):
        core.create_workspace('proj-x', {})
    with pytest.raises(ValueError, match="'default' already exists"):
        core.create_workspace('default', {})


def test_read_only_viewers_see_the_name(monkeypatch):
    workspaces = {
        'team-a': {
            'private': True,
            'allowed_users': ['alice'],
            'name': 'proj-a',
        }
    }
    monkeypatch.setattr(core, '_load_workspaces', lambda: workspaces)
    monkeypatch.setattr(
        core.permission, 'permission_service',
        mock.Mock(get_workspace_access_sets=lambda *a: ({'team-a'}, set())))
    monkeypatch.setattr(core.workspaces_utils, 'get_read_only_workspace_names',
                        lambda: {'team-a'})

    result = core.workspaces_for_user('bob')

    assert result['team-a'] == {
        'writable': False,
        'read_only': True,
        'private': True,
        'name': 'proj-a',
    }
