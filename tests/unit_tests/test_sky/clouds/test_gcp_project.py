"""Test project selection for GCP CLI commands."""

import json
import shlex
from unittest import mock

import pytest

from sky import global_user_state
from sky import skypilot_config
from sky.catalog import gcp_catalog
from sky.clouds import gcp as gcp_mod
from sky.clouds.utils import gcp_utils
from sky.provision.gcp import instance_utils
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
            global_user_state, 'get_handle_from_cluster_name',
            return_value=None), mock.patch.object(
                gcp_mod.subprocess_utils,
                'run_with_retries',
                side_effect=[
                    (0, '[{"name": "source-vm"}]', ''), (0, '', ''),
                    (0, f'https://www.googleapis.com/compute/v1/{image_id}\n',
                     '')
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


def test_create_image_uses_saved_cluster_project(tmp_path):
    cluster_yaml = tmp_path / 'source.yaml'
    assert not cluster_yaml.exists()
    handle = mock.Mock(cluster_yaml=str(cluster_yaml))
    image_id = 'projects/saved-project/global/images/clone-image'
    with mock.patch.object(global_user_state,
                           'get_handle_from_cluster_name',
                           return_value=handle) as get_handle, \
            mock.patch.object(
                global_user_state,
                'get_cluster_yaml_dict',
                return_value={'provider': {'project_id': 'saved-project'}}
            ) as get_yaml, \
            mock.patch.object(gcp_mod.GCP,
                              'get_project_id',
                              return_value='workspace-b') as get_project, \
            mock.patch.object(
                gcp_mod.subprocess_utils,
                'run_with_retries',
                side_effect=[
                    (0, '[{"name": "source-vm"}]', ''), (0, '', ''),
                    (0, f'https://www.googleapis.com/compute/v1/{image_id}', '')
                ]) as run:
        result = gcp_mod.GCP.create_image_from_cluster(
            resources_utils.ClusterName(display_name='source',
                                        name_on_cloud='source-cloud'),
            'us-central1', 'us-central1-a')

    assert result == image_id
    commands = [shlex.split(call.args[0]) for call in run.call_args_list]
    assert len(commands) == 3
    for command in commands:
        assert command[command.index('--project') + 1] == 'saved-project'
    get_handle.assert_called_once_with('source')
    get_yaml.assert_called_once_with(str(cluster_yaml))
    get_project.assert_not_called()


@pytest.mark.parametrize('handle, config, read_error', [
    (None, None, None),
    (mock.Mock(cluster_yaml=None), None, None),
    (mock.Mock(cluster_yaml='source.yaml'), {}, None),
    (mock.Mock(cluster_yaml='source.yaml'), {
        'provider': {}
    }, None),
    (mock.Mock(cluster_yaml='source.yaml'), {
        'provider': {
            'project_id': None
        }
    }, None),
    (mock.Mock(cluster_yaml='source.yaml'), {
        'provider': {
            'project_id': ''
        }
    }, None),
    (mock.Mock(cluster_yaml='source.yaml'), None,
     ValueError('Cluster yaml source.yaml not found.')),
    (mock.Mock(cluster_yaml='source.yaml'), None, RuntimeError('Read failed')),
])
def test_create_image_falls_back_without_saved_project(handle, config,
                                                       read_error):
    image_id = 'projects/workspace-project/global/images/clone-image'
    with mock.patch.object(global_user_state,
                           'get_handle_from_cluster_name',
                           return_value=handle), \
            mock.patch.object(global_user_state,
                              'get_cluster_yaml_dict',
                              return_value=config,
                              side_effect=read_error) as get_yaml, \
            mock.patch.object(gcp_mod.GCP,
                              'get_project_id',
                              return_value='workspace-project') as fallback, \
            mock.patch.object(
                gcp_mod.subprocess_utils,
                'run_with_retries',
                side_effect=[
                    (0, '[{"name": "source-vm"}]', ''), (0, '', ''),
                    (0, f'https://www.googleapis.com/compute/v1/{image_id}', '')
                ]) as run:
        result = gcp_mod.GCP.create_image_from_cluster(
            resources_utils.ClusterName(display_name='source',
                                        name_on_cloud='source-cloud'),
            'us-central1', 'us-central1-a')

    assert result == image_id
    assert run.call_count == 3
    for call in run.call_args_list:
        command = shlex.split(call.args[0])
        assert command[command.index('--project') + 1] == 'workspace-project'
    fallback.assert_called_once_with()
    if handle is None or handle.cluster_yaml is None:
        get_yaml.assert_not_called()
    else:
        get_yaml.assert_called_once_with(handle.cluster_yaml)


def test_provisioning_reservations_use_saved_cluster_project():
    # pylint: disable=protected-access
    reservation_name = 'projects/saved-project/reservations/reserved'
    reservation = gcp_utils.GCPReservation(
        self_link='https://www.googleapis.com/compute/v1/projects/'
        'saved-project/zones/us-central1-a/reservations/reserved',
        specific_reservation=gcp_utils.SpecificReservation(count=1,
                                                           in_use_count=0),
        specific_reservation_required=True,
        zone='us-central1-a')
    with mock.patch.object(gcp_mod.GCP,
                           'get_project_id',
                           return_value='workspace-project') as get_project, \
            mock.patch.object(
                gcp_utils,
                'list_reservations_for_instance_type_in_zone',
                return_value=[reservation]) as list_reservations, \
            mock.patch.object(instance_utils,
                              '_is_reservation_bound',
                              return_value=False), \
            mock.patch.object(instance_utils.GCPComputeInstance,
                              '_create_instances',
                              return_value=None) as create:
        errors, names = instance_utils.GCPComputeInstance.create_instances(
            cluster_name='source',
            project_id='saved-project',
            zone='us-central1-a',
            node_config={
                'machineType': 'n1-standard-8',
                'reservationAffinity': {
                    'values': [reservation_name]
                },
            },
            labels={},
            count=1,
            total_count=1,
            include_head_node=True)

    assert errors is None
    assert len(names) == 1
    create.assert_called_once()
    assert create.call_args.args[1] == 'saved-project'
    assert create.call_args.args[3]['reservationAffinity']['values'] == [
        reservation_name
    ]
    list_reservations.assert_called_once_with('n1-standard-8', 'us-central1-a',
                                              'saved-project')
    get_project.assert_not_called()
