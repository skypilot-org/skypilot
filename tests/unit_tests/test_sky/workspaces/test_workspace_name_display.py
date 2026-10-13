"""Users see workspace names; records and the server keep ids."""

from unittest import mock

from click import testing as click_testing
import fastapi

from sky import core
from sky import models
from sky import skypilot_config
from sky.client import sdk
from sky.client.cli import command
from sky.jobs import state as managed_job_state
from sky.jobs import utils as jobs_utils
from sky.jobs.server import core as jobs_core
from sky.users import server as users_server
from sky.utils import config_utils
from sky.utils.cli_utils import status_utils
from sky.workspaces import core as workspaces_core

_WORKSPACES = {'team-a': {'name': 'proj-a'}, 'team-b': {}}


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _job_row(job_id, workspace, workspace_name):
    return {
        'job_id': job_id,
        'task_id': 0,
        'task_name': f'task-{job_id}',
        'job_name': f'job-{job_id}',
        'workspace': workspace,
        'workspace_name': workspace_name,
        'resources': '1x[CPU:1]',
        'submitted_at': 1_700_000_000.0,
        'end_at': None,
        'job_duration': 10,
        'recovery_count': 0,
        'status': managed_job_state.ManagedJobStatus('RUNNING'),
        'schedule_state': 'ALIVE',
        'failure_reason': None,
        'details': None,
        'pool': None,
    }


def test_job_table_shows_workspace_names():
    rows = [_job_row(1, 'team-a', 'proj-a'), _job_row(2, 'team-b', 'team-b')]
    table = jobs_utils.format_job_table(rows,
                                        show_all=False,
                                        show_user=False,
                                        return_rows=True)
    cells = {str(cell) for row in table for cell in row}
    assert 'proj-a' in cells
    assert 'team-a' not in cells


def test_status_table_shows_the_name_with_old_server_fallback():
    assert status_utils._get_workspace({
        'workspace': 'team-a',
        'workspace_name': 'proj-a'
    }) == 'proj-a'
    # An older server sends no name.
    assert status_utils._get_workspace({'workspace': 'team-a'}) == 'team-a'


def test_status_records_carry_the_name():
    clusters = [{'name': 'c1', 'workspace': 'team-a'}]
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'workspaces': _WORKSPACES})), \
            mock.patch.object(core.backend_utils, 'get_clusters',
                              return_value=clusters), \
            mock.patch.object(core.responses.StatusResponse,
                              'model_validate', side_effect=lambda c: c):
        records = _unwrap(core.status)()
    assert records[0]['workspace_name'] == 'proj-a'


def test_managed_job_records_carry_the_name():
    jobs = [{'job_id': 1, 'task_id': 0, 'workspace': 'team-a'}]
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'workspaces': _WORKSPACES})), \
            mock.patch.object(jobs_core, 'queue_v2',
                              return_value=(jobs, 1, {}, 1, [])):
        records, *_ = _unwrap(jobs_core.queue_v2_api)(refresh=False)
    assert records[0].workspace_name == 'proj-a'


def test_workspace_info_prints_names():
    payload = {
        'workspace': 'team-a',
        'source': 'preferred',
        'note': None,
        'preferred': 'team-a',
        'accessible': ['default', 'team-a'],
        'names': {
            'default': 'default',
            'team-a': 'proj-a'
        },
    }
    with mock.patch.object(sdk, 'get_user_workspace', return_value=payload):
        result = click_testing.CliRunner().invoke(command.cli,
                                                  ['workspace', 'info'])
    assert result.exit_code == 0, result.output
    assert "Workspace: 'proj-a'" in result.output
    assert "Preferred: 'proj-a'" in result.output
    assert "Writable: 'default', 'proj-a'" in result.output


def test_names_only_cover_workspaces_the_user_can_see():
    user = models.User(id='alice', name='alice', preferred_workspace='team-a')
    request = mock.MagicMock(spec=fastapi.Request)
    request.state.auth_user = user
    resolution = workspaces_core.WorkspaceResolution(workspace='team-b',
                                                     source='default-fallback')
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'workspaces': _WORKSPACES})), \
            mock.patch.object(users_server.server_common,
                              'refresh_workspace_state_for_sync_handler'), \
            mock.patch.object(users_server.global_user_state, 'get_user',
                              return_value=user), \
            mock.patch.object(workspaces_core, 'resolve_workspace_for_user',
                              return_value=resolution), \
            mock.patch.object(workspaces_core, 'get_workspace_access_sets',
                              return_value=({'team-b'}, {'team-b'})):
        response = users_server.get_user_workspace(request)
    # Preferred 'team-a' is no longer visible, so its new name stays hidden.
    assert response['names'] == {'team-b': 'team-b'}
