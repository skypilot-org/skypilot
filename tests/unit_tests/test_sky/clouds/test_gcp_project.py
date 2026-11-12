"""Test project selection for GCP CLI commands."""

import json
import shlex
from unittest import mock

import pytest

from sky import skypilot_config
from sky.catalog import gcp_catalog
from sky.clouds import gcp as gcp_mod
from sky.clouds.utils import gcp_utils
from sky.utils import resources_utils


@pytest.fixture(name='launch_project', params=['workspace', 'adc'])
def mock_launch_project(request, monkeypatch):
    monkeypatch.setenv('CLOUDSDK_CORE_PROJECT', 'gcloud-default-project')
    config = {
        'project_id': 'launch-project'
    } if request.param == 'workspace' else {}
    monkeypatch.setattr(skypilot_config, 'get_workspace_cloud',
                        lambda _: config)
    monkeypatch.setattr(
        gcp_mod, '_get_default', lambda:
        (None, 'launch-project' if request.param == 'adc' else 'adc-project'))
    return 'launch-project'


@pytest.mark.parametrize('quota, expected', [(0, False), (16, True)])
def test_quota_command_uses_launch_project(launch_project, quota, expected):
    resources = mock.Mock(accelerators={'T4': 1},
                          use_spot=False,
                          region='us-central1')
    with mock.patch.object(gcp_catalog,
                           'get_quota_code',
                           return_value='NVIDIA_T4_GPUS'), mock.patch.object(
                               gcp_mod.subprocess_utils, 'run') as run:
        run.return_value.stdout = f'- limit: {quota}.0'.encode()
        assert gcp_mod.GCP.check_quota_available(resources) is expected

    command = shlex.split(run.call_args.kwargs['cmd'].split('|')[0])
    assert command[:5] == [
        'gcloud', 'compute', 'regions', 'describe', 'us-central1'
    ]
    assert command[command.index('--project') + 1] == launch_project


def test_create_image_commands_use_launch_project(launch_project):
    image_id = f'projects/{launch_project}/global/images/clone-image'
    with mock.patch.object(
            gcp_mod.subprocess_utils,
            'run_with_retries',
            side_effect=[
                (0, '[{"name": "source-vm"}]', ''), (0, '', ''),
                (0, f'https://www.googleapis.com/compute/v1/{image_id}\n', '')
            ]) as run:
        result = gcp_mod.GCP.create_image_from_cluster(
            resources_utils.ClusterName(display_name='source',
                                        name_on_cloud='source-cloud'),
            'us-central1', 'us-central1-a')

    assert result == image_id
    commands = [shlex.split(call.args[0]) for call in run.call_args_list]
    assert [command[2:4] for command in commands] == [['instances', 'list'],
                                                      ['images', 'create'],
                                                      ['images', 'describe']]
    for command in commands:
        assert command[command.index('--project') + 1] == launch_project
    assert commands[1][commands[1].index('--source-disk') + 1] == 'source-vm'


def test_delete_image_uses_image_project(launch_project):
    image_id = 'projects/image-project/global/images/clone-image'
    with mock.patch.object(gcp_mod.subprocess_utils,
                           'run_with_retries',
                           return_value=(0, '', '')) as run:
        gcp_mod.GCP.delete_image(image_id, 'us-central1')

    command = shlex.split(run.call_args.args[0])
    assert command[:5] == [
        'gcloud', 'compute', 'images', 'delete', 'clone-image'
    ]
    assert command[command.index('--project') + 1] == 'image-project'
    assert launch_project != 'image-project'
    assert '--quiet' in command


def test_reservations_use_launch_project_and_cache_by_project(launch_project):
    # pylint: disable=protected-access
    reservation = {
        'selfLink':
            f'https://www.googleapis.com/compute/v1/projects/'
            f'{launch_project}/zones/us-central1-a/reservations/reserved',
        'zone': 'https://www.googleapis.com/compute/v1/projects/'
                f'{launch_project}/zones/us-central1-a',
        'specificReservation': {
            'count': '2',
            'inUseCount': '1'
        },
        'specificReservationRequired': False,
    }
    gcp_utils._list_reservations_for_instance_type.cache_clear()
    try:
        with mock.patch.object(skypilot_config,
                               'get_effective_region_config',
                               return_value=True), mock.patch.object(
                                   gcp_mod.subprocess_utils,
                                   'run_with_retries',
                                   side_effect=[(0, json.dumps([reservation]),
                                                 ''), (0, '[]', '')]) as run:
            for _ in range(2):
                assert gcp_mod.GCP().get_reservations_available_resources(
                    'n1-standard-8', 'us-central1', 'us-central1-a', set()) == {
                        f'projects/{launch_project}/reservations/reserved': 1
                    }
            assert run.call_count == 1
            with mock.patch.object(gcp_mod.GCP,
                                   'get_project_id',
                                   return_value='other-project'):
                assert gcp_mod.GCP().get_reservations_available_resources(
                    'n1-standard-8', 'us-central1', 'us-central1-a',
                    set()) == {}

        commands = [shlex.split(call.args[0]) for call in run.call_args_list]
        assert [
            command[command.index('--project') + 1] for command in commands
        ] == [launch_project, 'other-project']
        assert all(command[:4] == ['gcloud', 'compute', 'reservations', 'list']
                   for command in commands)
    finally:
        gcp_utils._list_reservations_for_instance_type.cache_clear()
