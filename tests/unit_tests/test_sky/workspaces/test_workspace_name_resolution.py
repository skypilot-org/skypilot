"""Only workspace names a user typed are resolved; ids are used as-is."""

from unittest import mock

import fastapi
import pytest

from sky import check as sky_check
from sky import core
from sky import exceptions
from sky import models
from sky import skypilot_config
from sky.server.requests import payloads
from sky.users import server as users_server
from sky.utils import config_utils
from sky.workspaces import core as workspaces_core

# 'team-a' was renamed to 'archive', then a new workspace took the name
# 'team-a'. Its old name is now another workspace's name.
_REUSED = {
    'team-a': {
        'name': 'archive'
    },
    'ws-0123456789ab': {
        'name': 'team-a'
    },
}


@pytest.fixture(name='server_workspaces')
def server_workspaces_fixture():

    def use(workspaces):
        return skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'workspaces': workspaces}))

    return use


def test_current_name_resolves_to_its_id():
    assert skypilot_config.resolve_workspace_ref('archive', _REUSED) == 'team-a'
    assert skypilot_config.resolve_workspace_ref('default',
                                                 _REUSED) == ('default')


def test_old_name_works_until_reused():
    renamed = {'team-a': {'name': 'archive'}}
    assert skypilot_config.resolve_workspace_ref('team-a', renamed) == 'team-a'
    # Once reused, the name belongs to the workspace that has it now.
    assert skypilot_config.resolve_workspace_ref('team-a',
                                                 _REUSED) == ('ws-0123456789ab')


def test_workspace_label_carries_name_and_id():
    assert skypilot_config.workspace_label('team-a',
                                           _REUSED) == ("'archive' (team-a)")
    assert skypilot_config.workspace_label('default', _REUSED) == "'default'"


def test_unknown_ref_is_returned_unchanged():
    assert skypilot_config.resolve_workspace_ref('nope', _REUSED) == 'nope'


def test_duplicate_names_fail_instead_of_picking_one():
    workspaces = {'ws-x': {'name': 'ws-y'}, 'ws-y': {}}
    with pytest.raises(ValueError, match="named 'ws-y'"):
        skypilot_config.resolve_workspace_ref('ws-y', workspaces)


def test_typed_active_workspace_becomes_the_id(server_workspaces):
    with server_workspaces(_REUSED):
        with skypilot_config.override_skypilot_config(
            {'active_workspace': 'archive'}):
            assert skypilot_config.get_active_workspace() == 'team-a'
            resolved = skypilot_config.resolved_config()
    assert resolved['active_workspace'] == 'team-a'
    # resolved_config() also runs on the client (client-side admin policies),
    # where the active workspace is still the typed name: it must not claim
    # an exact id. Controller configs add the marker themselves.
    assert 'active_workspace_id' not in resolved


def test_active_workspace_id_is_exact(server_workspaces):
    # A recovering job holds the id 'team-a'; the workspace now *named*
    # 'team-a' must not capture it.
    with server_workspaces(_REUSED):
        with skypilot_config.override_skypilot_config({
                'active_workspace': 'team-a',
                'active_workspace_id': 'team-a'
        }):
            assert skypilot_config.get_active_workspace() == 'team-a'
        with skypilot_config.override_skypilot_config(
            {'active_workspace': 'team-a'}):
            assert skypilot_config.get_active_workspace() == 'ws-0123456789ab'


def test_server_config_active_workspace_id_does_not_beat_the_request():
    server = config_utils.Config({
        'workspaces': _REUSED,
        'active_workspace_id': 'team-a'
    })
    with skypilot_config.replace_skypilot_config_in_process(server):
        with skypilot_config.override_skypilot_config(
            {'active_workspace': 'ws-0123456789ab'}):
            assert skypilot_config.get_active_workspace() == 'ws-0123456789ab'


def test_unknown_active_workspace_still_fails(server_workspaces):
    with server_workspaces(_REUSED):
        with pytest.raises(ValueError, match='nope does not exist'):
            with skypilot_config.override_skypilot_config(
                {'active_workspace': 'nope'}):
                pass


def test_unset_active_workspace_stays_unset(server_workspaces):
    with server_workspaces(_REUSED):
        with skypilot_config.override_skypilot_config({'jobs': {}}):
            assert not skypilot_config.is_active_workspace_set()


def test_check_resolves_the_typed_name(server_workspaces):
    with server_workspaces(_REUSED), mock.patch.object(sky_check,
                                                       'check') as check:
        sky_check.check_for_request(workspace='archive')
    assert check.call_args.kwargs['workspace'] == 'team-a'


def test_enabled_clouds_resolves_only_the_typed_field(server_workspaces):
    with server_workspaces(_REUSED), mock.patch.object(
            core, 'enabled_clouds') as enabled_clouds:
        core.enabled_clouds_for_request(workspace='team-a')
        assert enabled_clouds.call_args.kwargs['workspace'] == (
            'ws-0123456789ab')
        # The dashboard sends the id; it is not resolved as a name.
        core.enabled_clouds_for_request(workspace_id='team-a')
        assert enabled_clouds.call_args.kwargs['workspace'] == 'team-a'


def test_permission_error_names_the_workspace_as_typed():
    user = models.User(id='bob', name='bob')
    with mock.patch.object(workspaces_core.permission.permission_service,
                           'check_workspace_permission',
                           return_value=False):
        with pytest.raises(Exception) as e:
            workspaces_core.check_workspace_permission(user,
                                                       'ws-0123456789ab',
                                                       workspace_ref='proj-a')
    assert "'proj-a'" in str(e.value)
    assert 'ws-0123456789ab' not in str(e.value)


def test_enabled_clouds_denial_names_the_workspace_as_typed():
    # A non-member types a private workspace's name: the denial must not
    # reveal its id.
    with mock.patch.object(skypilot_config,
                           'resolve_workspace_ref',
                           return_value='ws-0123456789ab'), \
            mock.patch.object(workspaces_core.permission.permission_service,
                              'check_workspace_permission',
                              return_value=False), \
            mock.patch.object(core.common_utils,
                              'get_current_user',
                              return_value=models.User(id='bob', name='bob')):
        with pytest.raises(exceptions.PermissionDeniedError) as e:
            core.enabled_clouds_for_request(workspace='proj-a')
    assert "'proj-a'" in str(e.value)
    assert 'ws-0123456789ab' not in str(e.value)


def test_requested_denial_names_the_workspace_as_typed():
    user = models.User(id='bob', name='bob')
    with mock.patch.object(workspaces_core.permission.permission_service,
                           'check_workspace_permission',
                           return_value=False), \
            mock.patch.object(workspaces_core,
                              '_try_resync_new_user_grants'):
        with pytest.raises(exceptions.PermissionDeniedError) as e:
            workspaces_core.resolve_workspace_for_user(
                user, requested='ws-0123456789ab', requested_ref='proj-a')
    assert "'proj-a'" in str(e.value)
    assert 'ws-0123456789ab' not in str(e.value)


def test_server_active_workspace_name_resolves_without_overrides():
    # An admin may type a name in the server's own config; a client that
    # sends no overrides must still land on the id.
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({
                'workspaces': _REUSED,
                'active_workspace': 'archive'
            })):
        with skypilot_config.override_skypilot_config(None):
            assert skypilot_config.get_active_workspace() == 'team-a'
            # The config editor still shows what the admin wrote.
            assert skypilot_config.to_dict()['active_workspace'] == 'archive'


def _request(user):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state.auth_user = user
    return request


def test_preferred_workspace_is_stored_as_the_id(server_workspaces):
    user = models.User(id='alice', name='alice')
    body = mock.MagicMock(spec=payloads.UserPreferredWorkspaceBody)
    body.preferred = 'archive'
    with server_workspaces(_REUSED), mock.patch.object(
            users_server.server_common,
            'refresh_workspace_state_for_sync_handler'), mock.patch.object(
                workspaces_core, 'set_user_preferred_workspace') as set_pref:
        users_server.set_user_preferred_workspace(_request(user), body)
    # The typed name rides along so a denial names it, not the id.
    set_pref.assert_called_once_with(user, 'team-a', workspace_ref='archive')


def test_requested_workspace_is_resolved_before_the_resolver(server_workspaces):
    user = models.User(id='alice', name='alice')
    resolution = workspaces_core.WorkspaceResolution(workspace='team-a',
                                                     source='explicit')
    with server_workspaces(_REUSED), mock.patch.object(
            users_server.server_common,
            'refresh_workspace_state_for_sync_handler'), mock.patch.object(
                users_server.global_user_state, 'get_user',
                return_value=user), mock.patch.object(
                    workspaces_core,
                    'resolve_workspace_for_user',
                    return_value=resolution) as resolve, mock.patch.object(
                        workspaces_core,
                        'get_accessible_workspace_names',
                        return_value={'team-a'}):
        users_server.get_user_workspace(_request(user), requested='archive')
    assert resolve.call_args.kwargs['requested'] == 'team-a'
    assert resolve.call_args.kwargs['requested_ref'] == 'archive'


def test_workspace_filter_narrows_by_name(monkeypatch):
    monkeypatch.setattr(workspaces_core, '_load_workspaces', lambda: _REUSED)
    ids = ['default', 'team-a', 'ws-0123456789ab']
    assert workspaces_core.filter_workspace_ids_by_name(ids,
                                                        'arch') == ['team-a']
    # Matched against names, so 'team' finds the workspace named 'team-a',
    # not the one whose id is 'team-a'.
    assert workspaces_core.filter_workspace_ids_by_name(
        ids, 'TEAM') == ['ws-0123456789ab']
    # A name wins over an exact id, as for `-w`.
    assert workspaces_core.filter_workspace_ids_by_name(
        ids, 'team-a') == ['ws-0123456789ab']
    assert workspaces_core.filter_workspace_ids_by_name(ids, 'nope') == []


def test_workspace_filter_matches_a_legacy_id_exactly(monkeypatch):
    # 'research' is the renamed workspace's old name, and still its id.
    monkeypatch.setattr(
        workspaces_core, '_load_workspaces', lambda: {
            'research': {
                'name': 'archive'
            },
            'ws-0123456789ab': {
                'name': 'cv'
            },
        })
    ids = ['default', 'research', 'ws-0123456789ab']
    assert workspaces_core.filter_workspace_ids_by_name(
        ids, 'research') == ['research']
    # Ids match exactly, never as a substring.
    assert workspaces_core.filter_workspace_ids_by_name(ids, 'resear') == []
    assert workspaces_core.filter_workspace_ids_by_name(ids, 'ws-0') == []


def test_ambiguous_server_active_workspace_falls_back_with_client_overrides():
    # A client that sends other overrides still gets the server's workspace.
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({
                'workspaces': {
                    'ws-x': {
                        'name': 'ws-y'
                    },
                    'ws-y': {}
                },
                'active_workspace': 'ws-y'
            })):
        with skypilot_config.override_skypilot_config(
            {'kubernetes': {
                'pod_config': {}
            }}):
            assert skypilot_config.get_active_workspace() == 'ws-y'
        # A name the user typed is still refused when ambiguous.
        with pytest.raises(ValueError):
            with skypilot_config.override_skypilot_config(
                {'active_workspace': 'ws-y'}):
                pass


def test_check_denial_names_the_workspace_as_typed(server_workspaces):
    with server_workspaces(_REUSED), mock.patch.object(
            workspaces_core.permission.permission_service,
            'check_workspace_permission',
            return_value=False), mock.patch.object(sky_check.common_utils,
                                                   'get_current_user',
                                                   return_value=models.User(
                                                       id='bob', name='bob')):
        with pytest.raises(exceptions.PermissionDeniedError) as e:
            sky_check.check_for_request(workspace='team-a')
    assert "'team-a'" in str(e.value)
    assert 'ws-0123456789ab' not in str(e.value)


def test_ambiguous_server_active_workspace_falls_back_to_the_raw_value():
    # Only a hand-edited config can name two workspaces alike.
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({
                'workspaces': {
                    'ws-x': {
                        'name': 'ws-y'
                    },
                    'ws-y': {}
                },
                'active_workspace': 'ws-y'
            })):
        with skypilot_config.override_skypilot_config(None):
            assert skypilot_config.get_active_workspace() == 'ws-y'


# Two named workspaces the user is a member of, and one the user lost.
_NAMED = {
    'ws-0123456789ab': {
        'name': 'ml'
    },
    'ws-ba9876543210': {
        'name': 'cv'
    },
    'ws-ffffffffffff': {
        'name': 'old-team'
    },
}


def _resolve_with_access(server_workspaces, preferred, readable):
    user = models.User(id='bob', name='bob', preferred_workspace=preferred)
    writable = {'ws-0123456789ab', 'ws-ba9876543210'}

    def accessible(user_id, names, action):
        del user_id
        return set(names) & (writable if action == 'write' else readable)

    with server_workspaces(_NAMED), mock.patch.object(
            workspaces_core, '_accessible_workspace_names_for_user',
            accessible):
        with pytest.raises(exceptions.WorkspaceAmbiguousError) as e:
            workspaces_core.resolve_workspace_for_user(user)
    return e.value


def test_ambiguous_error_lists_names(server_workspaces):
    error = _resolve_with_access(server_workspaces, None, set())
    assert 'ml' in str(error) and 'cv' in str(error)
    assert 'ws-0123456789ab' not in str(error)
    assert 'ws-ba9876543210' not in str(error)


def test_drift_note_names_a_preferred_workspace_still_readable(
        server_workspaces):
    error = _resolve_with_access(server_workspaces, 'ws-ffffffffffff',
                                 {'ws-ffffffffffff'})
    assert error.note == "preferred 'old-team' not accessible"


def test_drift_note_hides_a_lost_workspaces_name(server_workspaces):
    error = _resolve_with_access(server_workspaces, 'ws-ffffffffffff', set())
    assert 'old-team' not in error.note
    assert 'ws-ffffffffffff' in error.note


def test_resync_log_labels_workspaces(server_workspaces):
    user = models.User(id='bob', name='bob')
    calls = []

    def accessible(user_id, names, action):
        del user_id, action
        # Nothing before the re-sync, one workspace after it.
        calls.append(names)
        return set() if len(calls) == 1 else {'ws-0123456789ab'} & set(names)

    with server_workspaces(_NAMED), mock.patch.object(
            workspaces_core, '_accessible_workspace_names_for_user',
            accessible), mock.patch.object(
                workspaces_core,
                '_try_resync_new_user_grants'), mock.patch.object(
                    workspaces_core.logger, 'info') as info:
        workspaces_core.resolve_workspace_for_user(user)
    assert "'ml' (ws-0123456789ab)" in info.call_args.args[0]


def test_name_lookups_do_not_copy_the_config():
    # They run per record (tables, logs); a deep copy of the whole config
    # per call made a 200-workspace names map take ~240 ms.
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'workspaces': {
                'team-a': {
                    'name': 'archive'
                }
            }})), mock.patch.object(config_utils.copy,
                                    'deepcopy',
                                    side_effect=AssertionError('copied')):
        assert skypilot_config.get_workspace_name('team-a') == 'archive'
        assert skypilot_config.workspace_label('team-a') == (
            "'archive' (team-a)")
        assert skypilot_config.resolve_workspace_ref('archive') == 'team-a'
