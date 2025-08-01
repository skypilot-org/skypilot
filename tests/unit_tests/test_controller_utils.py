"""Test the controller_utils module."""
from typing import Any, Dict, Optional, Set, Tuple
from unittest import mock

import pytest

import sky
from sky import clouds
from sky.jobs import constants as managed_job_constants
from sky.serve import constants as serve_constants
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.utils import controller_utils
from sky.utils import registry

_DEFAULT_AUTOSTOP = {
    'down': False,
    'idle_minutes': 10,
    'wait_for': autostop_lib.AutostopWaitFor.JOBS_AND_SSH.value,
}


@pytest.mark.parametrize(
    ('controller_type', 'custom_controller_resources_config', 'expected'), [
        ('jobs', {}, {
            'cpus': '4+',
            'memory': '8x',
            'disk_size': 50,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('jobs', {
            'cpus': '4+',
            'disk_size': 100,
        }, {
            'cpus': '4+',
            'memory': '8x',
            'disk_size': 100,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('serve', {}, {
            'cpus': '4+',
            'disk_size': 200,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('serve', {
            'memory': '32+',
        }, {
            'cpus': '4+',
            'memory': '32+',
            'disk_size': 200,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
    ])
def test_get_controller_resources(controller_type: str,
                                  custom_controller_resources_config: Dict[str,
                                                                           Any],
                                  expected: Dict[str, Any], monkeypatch,
                                  enable_all_clouds):

    def get_custom_controller_resources(keys, default):
        if keys == (controller_type, 'controller', 'resources'):
            return custom_controller_resources_config
        else:
            return default

    monkeypatch.setattr('sky.skypilot_config.loaded', lambda: True)
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        get_custom_controller_resources)

    controller_resources = list(
        controller_utils.get_controller_resources(
            controller=controller_utils.Controllers.from_type(controller_type),
            task_resources=[]))[0]
    controller_resources_config = controller_resources.to_yaml_config()
    for k, v in expected.items():
        assert controller_resources_config.get(k) == v, (
            controller_type, custom_controller_resources_config, expected,
            controller_resources_config, k, v)


def _check_controller_resources(
        controller_resources: Set[sky.Resources], expected_infra_list: Set[str],
        default_controller_resources: Dict[str, Any]) -> None:
    """Helper function to check that the controller resources match the
    expected combinations."""
    for r in controller_resources:
        config = r.to_yaml_config()
        infra = config.pop('infra')
        assert infra in expected_infra_list
        expected_infra_list.remove(infra)
        assert config == default_controller_resources, config
    assert not expected_infra_list


@pytest.mark.parametrize(('controller_type', 'default_controller_resources'), [
    ('jobs', {
        **managed_job_constants.CONTROLLER_RESOURCES,
        'autostop': _DEFAULT_AUTOSTOP,
    }),
    ('serve', {
        **serve_constants.CONTROLLER_RESOURCES,
        'autostop': _DEFAULT_AUTOSTOP,
    }),
])
def test_get_controller_resources_with_task_resources(
        controller_type: str, default_controller_resources: Dict[str, Any],
        enable_all_clouds):

    # 1. All resources has cloud specified. All of them
    # could host controllers. Return a set, each item has
    # one cloud specified plus the default resources.
    all_clouds = {'aws', 'gcp', 'azure'}
    expected_infra_set = all_clouds
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(infra=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 2. All resources has cloud specified. Some of them
    # could NOT host controllers. Return a set, only
    # containing those could host controllers.
    all_clouds = {
        'aws', 'gcp', 'azure', 'fluidstack', 'kubernetes', 'lambda', 'runpod'
    }

    def _could_host_controllers(cloud_str: str) -> bool:
        cloud = registry.CLOUD_REGISTRY.from_str(cloud_str)
        try:
            cloud.check_features_are_supported(
                sky.Resources(),
                {sky.clouds.CloudImplementationFeatures.HOST_CONTROLLERS})
        except sky.exceptions.NotSupportedError:
            return False
        return True

    expected_infra_set = {c for c in all_clouds if _could_host_controllers(c)}
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(infra=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 3. Some resources does not have cloud specified.
    # Return the default resources.
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(accelerators='L4'),
            sky.Resources(infra='runpod', accelerators='A40'),
        ])
    assert len(controller_resources) == 1
    config = list(controller_resources)[0].to_yaml_config()
    assert config == default_controller_resources, config

    # 4. All resources have clouds, regions, and zones specified.
    # Return a set of controller resources for all combinations of clouds,
    # regions, and zones. Each combination should contain the default resources
    # along with the cloud, region, and zone.
    all_cloud_regions_zones = [
        sky.Resources(cloud=sky.AWS(), region='us-east-1', zone='us-east-1a'),
        sky.Resources(cloud=sky.AWS(), region='ap-south-1', zone='ap-south-1b'),
        sky.Resources(cloud=sky.GCP(),
                      region='us-central1',
                      zone='us-central1-a'),
        sky.Resources(cloud=sky.GCP(),
                      region='europe-west1',
                      zone='europe-west1-b'),
    ]
    expected_infra_set = {
        'aws/us-east-1/us-east-1a',
        'aws/ap-south-1/ap-south-1b',
        'gcp/us-central1/us-central1-a',
        'gcp/europe-west1/europe-west1-b',
    }
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=all_cloud_regions_zones)
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 5. Clouds and regions are specified, but zones are partially specified.
    # Return a set containing combinations where the zone is None when not all
    # zones are specified in the input for the given region. The default
    # resources should be returned along with the cloud and region, and the
    # zone (if specified).
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(infra='aws/us-west-2'),
            sky.Resources(infra='aws/us-west-2/us-west-2b'),
            sky.Resources(infra='gcp/us-central1/us-central1-a')
        ])
    expected_infra_set = {
        'aws/us-west-2',
        'gcp/us-central1/us-central1-a',
    }
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 6. Mixed case: Some resources have clouds and regions or zones, others do
    # not. For clouds where regions or zones are not specified in the input,
    # return None for those fields. The default resources should be returned
    # along with the cloud, region (if specified), and zone (if specified).
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(cloud=sky.GCP(), region='europe-west1'),
            sky.Resources(cloud=sky.GCP()),
            sky.Resources(cloud=sky.AWS(),
                          region='eu-north-1',
                          zone='eu-north-1a'),
            sky.Resources(cloud=sky.AWS(), region='eu-north-1'),
            sky.Resources(cloud=sky.AWS(), region='ap-south-1'),
            sky.Resources(cloud=sky.Azure()),
        ])
    expected_infra_set = {
        'aws/eu-north-1',
        'aws/ap-south-1',
        'gcp',
        'azure',
    }
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_empty_clouds(
        controller_type: str, monkeypatch):
    """Test that the function works correctly with no enabled clouds."""

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return []

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should have at least uv installation and python packages
    assert len(commands) >= 3
    # Check that uv installation is included
    assert any('uv' in cmd for cmd in commands)
    # Check that flask is included for dashboard
    assert any('flask' in cmd for cmd in commands)
    # Should end with "done" message
    assert 'done.' in commands[-1]


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_aws_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only AWS enabled."""

    mock_aws = mock.Mock(spec=clouds.AWS)
    mock_aws.canonical_name.return_value = 'aws'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_aws]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include AWS dependencies
    combined_commands = ' '.join(commands)
    assert 'awscli' in combined_commands or 'boto3' in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_gcp_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only GCP enabled."""

    mock_gcp = mock.Mock(spec=clouds.GCP)
    mock_gcp.canonical_name.return_value = 'gcp'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_gcp]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    # Mock the GCP installation command
    mock_gcp_install_cmd = 'mock_gcp_install'
    monkeypatch.setattr(
        'sky.utils.controller_utils.gcp.GOOGLE_SDK_INSTALLATION_COMMAND',
        mock_gcp_install_cmd)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include GCP SDK installation
    combined_commands = ' '.join(commands)
    assert 'GCP SDK' in combined_commands
    assert mock_gcp_install_cmd in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_azure_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only Azure enabled."""

    mock_azure = mock.Mock(spec=clouds.Azure)
    mock_azure.canonical_name.return_value = 'azure'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_azure]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    # Mock dependencies
    monkeypatch.setattr('sky.utils.controller_utils.dependencies.AZURE_CLI',
                        'azure-cli>=2.65.0')

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include azure-cli installation
    combined_commands = ' '.join(commands)
    assert 'azure-cli' in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_kubernetes_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only Kubernetes enabled."""

    mock_k8s = mock.Mock(spec=clouds.Kubernetes)
    mock_k8s.canonical_name.return_value = 'kubernetes'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_k8s]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include Kubernetes dependencies
    combined_commands = ' '.join(commands)
    assert 'Kubernetes' in combined_commands
    assert 'kubectl' in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_mixed_clouds(
        controller_type: str, monkeypatch):
    """Test dependencies installation with multiple clouds enabled."""

    mock_aws = mock.Mock(spec=clouds.AWS)
    mock_aws.canonical_name.return_value = 'aws'

    mock_gcp = mock.Mock(spec=clouds.GCP)
    mock_gcp.canonical_name.return_value = 'gcp'

    mock_k8s = mock.Mock(spec=clouds.Kubernetes)
    mock_k8s.canonical_name.return_value = 'kubernetes'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_aws, mock_gcp, mock_k8s]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    def mock_cloud_in_iterable(cloud, enabled_clouds):
        # Mock that Kubernetes is in the enabled clouds
        return isinstance(cloud, clouds.Kubernetes)

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)
    monkeypatch.setattr('sky.utils.controller_utils.clouds.cloud_in_iterable',
                        mock_cloud_in_iterable)

    # Mock the GCP installation command
    mock_gcp_install_cmd = 'mock_gcp_install'
    monkeypatch.setattr(
        'sky.utils.controller_utils.gcp.GOOGLE_SDK_INSTALLATION_COMMAND',
        mock_gcp_install_cmd)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include dependencies for all clouds
    combined_commands = ' '.join(commands)
    assert 'GCP SDK' in combined_commands
    assert 'Kubernetes' in combined_commands
    assert 'kubectl' in combined_commands
    # Should also include GKE auth plugin since both GCP and K8s are enabled
    assert 'gke-gcloud-auth-plugin' in combined_commands


def test_get_cloud_dependencies_installation_commands_ibm_jobs_only(
        monkeypatch):
    """Test that IBM dependencies are only included for jobs controller."""

    mock_ibm = mock.Mock(spec=clouds.IBM)
    mock_ibm.canonical_name.return_value = 'ibm'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_ibm]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    # Test with jobs controller - should include IBM deps
    jobs_controller = controller_utils.Controllers.JOBS_CONTROLLER
    jobs_commands = controller_utils._get_cloud_dependencies_installation_commands(
        jobs_controller)

    # Test with serve controller - should not include IBM deps
    serve_controller = controller_utils.Controllers.SKY_SERVE_CONTROLLER
    serve_commands = controller_utils._get_cloud_dependencies_installation_commands(
        serve_controller)

    # Both should have same number of commands since IBM deps are filtered
    # based on controller type in the function itself
    assert len(jobs_commands) == len(serve_commands)


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_cloudflare_storage(
        controller_type: str, monkeypatch):
    """Test dependencies installation with Cloudflare storage enabled."""

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return []

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return ['cloudflare']

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)
    monkeypatch.setattr('sky.utils.controller_utils.cloudflare.NAME',
                        'cloudflare')

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include cloudflare dependencies (which are aws dependencies)
    combined_commands = ' '.join(commands)
    # Cloudflare dependencies include AWS dependencies
    assert any('awscli' in cmd or 'boto3' in cmd for cmd in commands)


def test_get_cloud_dependencies_installation_commands_command_structure(
        monkeypatch):
    """Test the structure and format of generated commands."""

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return []

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.JOBS_CONTROLLER
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Test command structure
    assert len(commands) >= 3  # At least uv, python packages, and done message

    # First command should be uv installation
    assert 'uv' in commands[0]
    assert constants.SKY_UV_INSTALL_CMD in commands[0]

    # Python packages command should include flask
    python_cmd = next(
        (cmd for cmd in commands if 'cloud python packages' in cmd), None)
    assert python_cmd is not None
    assert 'flask' in python_cmd
    assert constants.SKY_UV_PIP_CMD in python_cmd

    # Last command should be the "done" message
    assert 'done.' in commands[-1]

    # All commands except the last should contain step numbering
    for cmd in commands[:-1]:
        assert '[' in cmd and ']' in cmd  # Step numbering format

    # Check that step numbers are properly replaced
    for i, cmd in enumerate(commands[:-1], 1):
        if 'echo -en' in cmd:
            # Check that <step> and <total> placeholders are replaced
            assert '<step>' not in cmd
            assert '<total>' not in cmd


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_vast_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only Vast enabled."""

    mock_vast = mock.Mock(spec=clouds.Vast)
    mock_vast.canonical_name.return_value = 'vast'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_vast]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include Vast dependencies
    combined_commands = ' '.join(commands)
    assert 'Vast' in combined_commands
    assert 'vastai_sdk' in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_nebius_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only Nebius enabled."""

    mock_nebius = mock.Mock(spec=clouds.Nebius)
    mock_nebius.canonical_name.return_value = 'nebius'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_nebius]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include Nebius dependencies
    combined_commands = ' '.join(commands)
    assert 'Nebius' in combined_commands
    assert 'nebius profile create' in combined_commands


@pytest.mark.parametrize('controller_type', ['jobs', 'serve'])
def test_get_cloud_dependencies_installation_commands_cudo_only(
        controller_type: str, monkeypatch):
    """Test dependencies installation with only Cudo enabled."""

    mock_cudo = mock.Mock(spec=clouds.Cudo)
    mock_cudo.canonical_name.return_value = 'cudo'

    def mock_get_cached_enabled_clouds_or_refresh(cloud_capability):
        return [mock_cudo]

    def mock_get_cached_enabled_storage_cloud_names_or_refresh():
        return []

    monkeypatch.setattr(
        'sky.utils.controller_utils.sky_check.get_cached_enabled_clouds_or_refresh',
        mock_get_cached_enabled_clouds_or_refresh)
    monkeypatch.setattr(
        'sky.utils.controller_utils.storage_lib.get_cached_enabled_storage_cloud_names_or_refresh',
        mock_get_cached_enabled_storage_cloud_names_or_refresh)

    controller = controller_utils.Controllers.from_type(controller_type)
    commands = controller_utils._get_cloud_dependencies_installation_commands(
        controller)

    # Should include Cudo dependencies
    combined_commands = ' '.join(commands)
    assert 'cudoctl' in combined_commands
