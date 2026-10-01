"""Tests for Vast cloud provider."""

from unittest import mock

from sky import clouds
from sky.clouds import vast
from sky.provision import common
from sky.provision import docker_utils
from sky.provision.vast import instance as vast_instance
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import yaml_utils


def test_deploy_variables_pass_use_spot():
    resources = mock.MagicMock(unsafe=True,
                               instance_type='1x-RTX_3090-8-32768',
                               image_id=None,
                               use_spot=True,
                               cluster_config_overrides={})
    resources.assert_launchable.return_value = resources
    with mock.patch.object(vast.Vast,
                           'get_accelerators_from_instance_type',
                           return_value={'RTX3090': 1}):
        deploy_vars = vast.Vast().make_deploy_resources_variables(
            resources, resources_utils.ClusterName('c', 'c'),
            clouds.Region('US'), None, 1)
    assert deploy_vars['use_spot'] is True


def test_docker_login_reaches_launch(tmp_path):
    cluster_yaml = str(tmp_path / 'vast.yml')
    common_utils.fill_template(
        'vast-ray.yml.j2', {
            'num_nodes': 1,
            'credentials': {},
            'image_id': 'org/image:tag',
            'docker_login_config': docker_utils.DockerLoginConfig(
                username='user', password='pass', server='ghcr.io'),
        }, cluster_yaml)
    config = yaml_utils.read_yaml(cluster_yaml)
    provision_config = common.ProvisionConfig(
        provider_config=config['provider'],
        authentication_config={},
        docker_config=config.get('docker', {}),
        node_config=config['available_node_types']['ray_head_default']
        ['node_config'],
        count=1,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None)
    head = {
        '1': {
            'id': '1',
            'name': 'c-head',
            'status': 'RUNNING',
            'ssh_port': 22
        }
    }
    with mock.patch.object(vast_instance.utils, 'list_instances',
                           side_effect=[{}, head, head]), \
         mock.patch.object(vast_instance.utils, 'launch',
                           return_value='1') as launch:
        vast_instance.run_instances('US', 'c', 'c', provision_config)
    assert launch.call_args.kwargs['login'] == '-u user -p pass ghcr.io'
    assert launch.call_args.kwargs['image_name'] == 'ghcr.io/org/image:tag'
    redacted = provision_config.get_redacted_config()
    assert redacted['provider_config']['docker_login_config'][
        'password'] == '<redacted>'
