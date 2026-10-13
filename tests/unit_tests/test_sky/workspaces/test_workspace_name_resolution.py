"""Only workspace names a user typed are resolved; ids are used as-is."""

from unittest import mock

import fastapi
import pytest

from sky import check as sky_check
from sky import core
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
    # Saved job configs carry the id marker, so recovery never re-resolves.
    assert resolved['active_workspace'] == 'team-a'
    assert resolved['active_workspace_id'] == 'team-a'


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
    set_pref.assert_called_once_with(user, 'team-a')


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
