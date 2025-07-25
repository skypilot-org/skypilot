import importlib
import os
from typing import Dict
from unittest import mock

import pytest

from sky import check
from sky import clouds
from sky import global_user_state
from sky import skypilot_config
from sky.clouds import cloud as sky_cloud
from sky.resources import Resources
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.utils import resources_utils

GLOBAL_VALID_LABELS = {
    'plaintext': 'plainvalue',
    'numbers123': '123',
}

GLOBAL_INVALID_LABELS = {
    'l' * 129: 'value',  # Long key
    'key': 'v' * 257,  # Long value
    'spaces in label': 'spaces in value',
    '': 'emptykey',
}


def test_get_reservations_available_resources():
    mock_cloud = mock.Mock()
    r = Resources(cloud=mock_cloud, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r.get_reservations_available_resources()
    mock_cloud.get_reservations_available_resources.assert_called_once_with(
        "instance_type", "region", "zone", set())


def _run_label_test(allowed_labels: Dict[str, str],
                    invalid_labels: Dict[str, str],
                    cloud: clouds.Cloud = None):
    """Run a test for labels with the given allowed and invalid labels."""
    r_allowed = Resources(cloud=cloud, labels=allowed_labels)  # Should pass
    r_allowed.validate()
    assert r_allowed.labels == allowed_labels, ('Allowed labels '
                                                'should be the same')

    # Check for each invalid label
    for invalid_label, value in invalid_labels.items():
        l = {invalid_label: value}
        r = Resources(cloud=cloud, labels=l)
        with pytest.raises(ValueError):
            r.validate()
            assert False, (f'Resources {r.to_yaml_config()} were initialized '
                           f'with invalid label {invalid_label}={value} but no '
                           'error was raised.')


def test_gcp_labels_resources():
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'domain/key': 'value',
        '1numericstart': 'value',
    }
    cloud = clouds.GCP()
    _run_label_test(allowed_labels, invalid_labels, cloud)


def test_aws_labels_resources():
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'aws:cannotstartwithaws': 'value',
    }
    cloud = clouds.AWS()
    _run_label_test(allowed_labels, invalid_labels, cloud)


def test_kubernetes_labels_resources():
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
        'kueue.x-k8s.io/queue-name': 'queue',
        'a' * 253 + '/' + 'k' * 63: 'v' *
                                    63,  # upto 253 chars in domain, 63 in key
        'mylabel': '',  # empty label values are allowed by k8s
        '1numericstart': 'value',  # numeric start is allowed by k8s
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'a' * 254 + '/' + 'k' * 64: 'v' *
                                    63,  # exceed 253 chars in domain, 63 in key
    }
    cloud = clouds.Kubernetes()
    _run_label_test(allowed_labels, invalid_labels, cloud)


def test_no_cloud_labels_resources():
    global_user_state.set_enabled_clouds(['aws', 'gcp'],
                                         sky_cloud.CloudCapability.COMPUTE,
                                         constants.SKYPILOT_DEFAULT_WORKSPACE)
    global_user_state.set_allowed_clouds(
        check._get_workspace_allowed_clouds(
            constants.SKYPILOT_DEFAULT_WORKSPACE),
        constants.SKYPILOT_DEFAULT_WORKSPACE)
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'aws:cannotstartwithaws': 'value',
        'domain/key': 'value',  # Invalid for GCP
    }
    _run_label_test(allowed_labels, invalid_labels)


def test_no_cloud_labels_resources_single_enabled_cloud():
    global_user_state.set_enabled_clouds(['aws'],
                                         sky_cloud.CloudCapability.COMPUTE,
                                         constants.SKYPILOT_DEFAULT_WORKSPACE)
    global_user_state.set_allowed_clouds(
        check._get_workspace_allowed_clouds(
            constants.SKYPILOT_DEFAULT_WORKSPACE),
        constants.SKYPILOT_DEFAULT_WORKSPACE)
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
        'domain/key': 'value',  # Valid for AWS
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'aws:cannotstartwithaws': 'value',
    }
    _run_label_test(allowed_labels, invalid_labels, cloud=clouds.AWS())


@mock.patch('sky.catalog.instance_type_exists', return_value=True)
@mock.patch('sky.catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.catalog.get_image_id_from_tag', return_value='fake-image')
@mock.patch.object(clouds.aws, 'DEFAULT_SECURITY_GROUP_NAME', 'fake-default-sg')
def test_aws_make_deploy_variables(*mocks) -> None:
    os.environ[
        skypilot_config.
        ENV_VAR_SKYPILOT_CONFIG] = './tests/test_yamls/test_aws_config.yaml'
    importlib.reload(skypilot_config)

    cloud = clouds.AWS()
    cluster_name = resources_utils.ClusterName(display_name='display',
                                               name_on_cloud='cloud')
    region = clouds.Region(name='fake-region')
    zones = [clouds.Zone(name='fake-zone')]
    resource = Resources(cloud=cloud, instance_type='fake-type: 3')
    config = resource.make_deploy_variables(cluster_name,
                                            region,
                                            zones,
                                            num_nodes=1,
                                            dryrun=True)

    expected_config_base = {
        'instance_type': resource.instance_type,
        'custom_resources': '{"fake-acc":2}',
        'use_spot': False,
        'region': 'fake-region',
        'image_id': 'fake-image',
        'disk_encrypted': False,
        'disk_tier': 'gp3',
        'disk_throughput': 218,
        'disk_iops': 3500,
        'docker_image': None,
        'docker_container_name': 'sky_container',
        'docker_login_config': None,
        'docker_run_options': [],
        'initial_setup_commands': [],
        'zones': 'fake-zone'
    }

    # test using defaults
    expected_config = expected_config_base.copy()
    expected_config.update({
        'security_group': 'fake-default-sg',
        'security_group_managed_by_skypilot': 'true'
    })
    assert config == expected_config, ('unexpected resource '
                                       'variables generated')

    # test using cluster matches regex, top
    cluster_name = resources_utils.ClusterName(
        display_name='sky-serve-fake1-1234', name_on_cloud='name-on-cloud')
    expected_config = expected_config_base.copy()
    expected_config.update({
        'security_group': 'fake-1-sg',
        'security_group_managed_by_skypilot': 'false'
    })
    config = resource.make_deploy_variables(cluster_name,
                                            region,
                                            zones,
                                            num_nodes=1,
                                            dryrun=True)
    assert config == expected_config, ('unexpected resource '
                                       'variables generated')

    # test using cluster matches regex, middle
    cluster_name = resources_utils.ClusterName(
        display_name='sky-serve-fake2-1234', name_on_cloud='name-on-cloud')
    expected_config = expected_config_base.copy()
    expected_config.update({
        'security_group': 'fake-2-sg',
        'security_group_managed_by_skypilot': 'false'
    })
    config = resource.make_deploy_variables(cluster_name,
                                            region,
                                            zones,
                                            num_nodes=1,
                                            dryrun=True)
    assert config == expected_config, ('unexpected resource '
                                       'variables generated')


@pytest.mark.parametrize(['resources_kwargs', 'expected_yaml_config'], [
    ({
        'infra': '*/*/us-east-1b',
        'accelerators': 'A10'
    }, {
        'infra': '*/*/us-east-1b',
        'accelerators': {
            'A10': 1
        },
        'disk_size': 256,
    }),
    ({
        'infra': 'gcp/*/us-east1-b',
        'accelerators': 'A10:8',
        'labels': {
            'key': 'value'
        }
    }, {
        'infra': 'gcp/*/us-east1-b',
        'accelerators': {
            'A10': 8
        },
        'labels': {
            'key': 'value'
        },
        'disk_size': 256,
    }),
])
def test_to_yaml_and_load(resources_kwargs, expected_yaml_config):
    r = Resources(**resources_kwargs)
    yaml_config = r.to_yaml_config()
    assert yaml_config == expected_yaml_config

    loaded_r = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_r.cloud == r.cloud
    assert loaded_r.region == r.region
    assert loaded_r.zone == r.zone
    original_accelerators = r.accelerators
    assert loaded_r.accelerators == original_accelerators
    assert original_accelerators == r.accelerators
    assert loaded_r.labels == r.labels


def test_resources_any_of():
    """Test Resources creation with any_of option."""
    # Test any_of with different resources options
    config = {
        'any_of': [
            {
                'cpus': 8,
                'memory': 16
            },
            {
                'cpus': 4,
                'memory': 32
            },
            {
                'accelerators': 'V100:1'
            },
        ]
    }
    resources_set = Resources.from_yaml_config(config)

    # Verify it returns a set of resources
    assert isinstance(resources_set, set)
    assert len(resources_set) == 3

    # Validate the resources options are correctly created
    resources_list = list(resources_set)

    # Find resources by properties (order may not be preserved)
    r_cpus8 = next((r for r in resources_list if r.cpus == '8'), None)
    r_cpus4 = next((r for r in resources_list if r.cpus == '4'), None)
    r_gpu = next((r for r in resources_list if r.accelerators is not None),
                 None)

    assert r_cpus8 is not None
    assert r_cpus8.memory == '16'

    assert r_cpus4 is not None
    assert r_cpus4.memory == '32'

    assert r_gpu is not None
    assert r_gpu.accelerators == {'V100': 1}


def test_resources_ordered():
    """Test Resources creation with ordered option."""
    # Test ordered with different resources options
    config = {
        'ordered': [
            {
                'infra': 'gcp',
                'accelerators': 'A100:8'
            },
            {
                'infra': 'aws',
                'accelerators': 'V100:8'
            },
            {
                'accelerators': 'T4:8'
            },
        ]
    }
    resources_list = Resources.from_yaml_config(config)

    # Verify it returns a list of resources
    assert isinstance(resources_list, list)
    assert len(resources_list) == 3

    # Ordered resources should preserve order
    assert resources_list[0].infra.cloud.lower() == 'gcp'
    assert resources_list[0].accelerators == {'A100': 8}

    assert resources_list[1].infra.cloud.lower() == 'aws'
    assert resources_list[1].accelerators == {'V100': 8}

    assert resources_list[2].accelerators == {'T4': 8}


def test_resources_any_of_spot_flag():
    """Test Resources with any_of option including spot flag variations."""
    config = {
        'accelerators': 'A100:8',
        'any_of': [{
            'use_spot': True
        }, {
            'use_spot': False
        }]
    }
    resources_set = Resources.from_yaml_config(config)

    # Verify it returns a set of resources
    assert isinstance(resources_set, set)
    assert len(resources_set) == 2

    # Find spot and on-demand resources
    resources_list = list(resources_set)
    r_spot = next((r for r in resources_list if r.use_spot), None)
    r_ondemand = next((r for r in resources_list if not r.use_spot), None)

    assert r_spot is not None
    assert r_spot.accelerators == {'A100': 8}
    assert r_spot.use_spot is True

    assert r_ondemand is not None
    assert r_ondemand.accelerators == {'A100': 8}
    assert r_ondemand.use_spot is False


def test_resources_ordered_preference():
    """Test Resources creation with ordered preference correctly preserves order."""
    config = {
        'ordered': [
            {
                'infra': 'aws/us-east-1',
                'accelerators': 'A100:8'
            },
            {
                'infra': 'gcp/us-central1',
                'accelerators': 'A100:8'
            },
            {
                'infra': 'azure/eastus',
                'accelerators': 'A100:8'
            },
        ]
    }
    resources_list = Resources.from_yaml_config(config)

    # Verify order matches the input order
    assert resources_list[0].infra.cloud.lower() == 'aws'
    assert resources_list[0].infra.region == 'us-east-1'

    assert resources_list[1].infra.cloud.lower() == 'gcp'
    assert resources_list[1].infra.region == 'us-central1'

    assert resources_list[2].infra.cloud.lower() == 'azure'
    assert resources_list[2].infra.region == 'eastus'


def test_resources_any_of_ordered_exclusive():
    """Test that Resources raises ValueError if both any_of and ordered are specified."""
    config = {'any_of': [{'cpus': 8}], 'ordered': [{'cpus': 4}]}

    # Should raise ValueError because both any_of and ordered are specified
    with pytest.raises(ValueError,
                       match='Cannot specify both "any_of" and "ordered"'):
        Resources.from_yaml_config(config)


def test_resources_any_of_with_base_infra():
    """Test Resources creation with any_of option and base infra."""
    # Test any_of with base infra and additional infra specifications
    config = {
        'infra': 'aws',  # Base infra
        'cpus': 8,
        'any_of': [
            {
                'infra': 'aws/us-east-1'
            },  # Override with specific region
            {
                'infra': 'aws/us-west-2'
            },  # Different region
            {
                'infra': 'gcp/us-central1'
            },  # Different cloud
        ]
    }
    resources_set = Resources.from_yaml_config(config)

    # Verify it returns a set of resources
    assert isinstance(resources_set, set)
    assert len(resources_set) == 3

    # Validate the resources are correctly created with proper infra
    resources_list = list(resources_set)

    # All resources should have cpus=8 from the base config
    for r in resources_list:
        assert r.cpus == '8'

    # Find resources by infra properties
    r_east = next((r for r in resources_list if r.infra.region == 'us-east-1'),
                  None)
    r_west = next((r for r in resources_list if r.infra.region == 'us-west-2'),
                  None)
    r_gcp = next((r for r in resources_list if r.infra.cloud.lower() == 'gcp'),
                 None)

    assert r_east is not None
    assert str(r_east.cloud).lower() == 'aws'

    assert r_west is not None
    assert str(r_west.cloud).lower() == 'aws'

    assert r_gcp is not None
    assert r_gcp.infra.region == 'us-central1'


def test_resources_ordered_with_base_infra():
    """Test Resources creation with ordered option and base infra."""
    # Test ordered with base infra and additional infra specifications
    config = {
        'infra': 'azure',  # Base infra
        'accelerators': 'A100:8',  # Base accelerator
        'ordered': [
            {
                'infra': 'gcp/us-central1'
            },  # Specific region in same cloud
            {
                'infra': 'aws/us-east-1'
            },  # Different cloud
            {
                'accelerators': 'T4:8'
            },  # Another cloud
        ]
    }
    resources_list = Resources.from_yaml_config(config)

    # Verify it returns a list of resources with right length
    assert isinstance(resources_list, list)
    assert len(resources_list) == 3

    # All resources should have A100:8 from the base config
    assert resources_list[0].accelerators == {'A100': 8}
    assert resources_list[1].accelerators == {'A100': 8}
    assert resources_list[2].accelerators == {'T4': 8}

    # Ordered resources should preserve order and have correct infra
    assert str(resources_list[0].cloud).lower() == 'gcp'
    assert resources_list[0].region == 'us-central1'

    assert str(resources_list[1].cloud).lower() == 'aws'
    assert resources_list[1].region == 'us-east-1'

    assert str(resources_list[2].cloud).lower() == 'azure'
    assert resources_list[2].region is None


def test_kubernetes_image_id_formats_in_resources(enable_all_clouds):
    """Test Resources normalizes Kubernetes image_id to include 'docker:' prefix."""
    k8s_cloud = clouds.Kubernetes()
    test_cases = [
        # (input_image_id, expected_stored_image_id_in_resources_object)
        ('alpine', 'docker:alpine'),
        ('docker:alpine', 'docker:alpine'),
        ('myrepo/myimage:latest', 'docker:myrepo/myimage:latest'),
        ('docker:myrepo/myimage:latest', 'docker:myrepo/myimage:latest'),
        ('another.registry.com/myrepo/myimage:tag',
         'docker:another.registry.com/myrepo/myimage:tag'),
        ('docker:another.registry.com/myrepo/myimage:tag',
         'docker:another.registry.com/myrepo/myimage:tag'),
    ]

    for input_id, expected_stored_id in test_cases:
        # Test direct initialization and validation
        # This assumes Resources.__init__ or a subsequent step normalizes
        # image_id for Kubernetes by adding 'docker:' if missing.
        res = Resources(cloud=k8s_cloud, image_id=input_id)
        res.validate(
        )  # Kubernetes cloud validate_resources currently just checks type
        assert list(res.image_id.values())[0] == expected_stored_id, (
            f'Input: {input_id}, Expected stored: {expected_stored_id}, '
            f'Got: {res.image_id}')

        # Test YAML serialization and deserialization
        # Assumes to_yaml_config() serializes the (potentially prefixed)
        # internal value.
        yaml_config = res.to_yaml_config()
        assert list(
            yaml_config.get('image_id').values())[0] == expected_stored_id, (
                f'Input: {input_id}, Expected in YAML: {expected_stored_id}, '
                f'Got in YAML: {yaml_config}')

        loaded_res_list = list(Resources.from_yaml_config(yaml_config))
        assert len(loaded_res_list) == 1, f"Load count for {input_id}"
        loaded_res = loaded_res_list[0]
        assert list(loaded_res.image_id.values())[0] == expected_stored_id, (
            f'Input: {input_id}, Expected from loaded YAML: {expected_stored_id}, '
            f'Got: {loaded_res.image_id}')
        assert isinstance(
            loaded_res.cloud,
            clouds.Kubernetes), (f'Loaded cloud type mismatch for {input_id}')


def test_network_tier_basic():
    """Test basic network tier functionality and validation."""
    # Test with no network_tier specified (defaults to None)
    r = Resources()
    assert r.network_tier is None

    # Test with standard network tier
    r = Resources(network_tier='standard')
    assert r.network_tier == resources_utils.NetworkTier.STANDARD

    # Test with best network tier
    r = Resources(network_tier='best')
    assert r.network_tier == resources_utils.NetworkTier.BEST

    # Test with NetworkTier enum directly
    r = Resources(network_tier=resources_utils.NetworkTier.BEST)
    assert r.network_tier == resources_utils.NetworkTier.BEST


def test_network_tier_validation():
    """Test network tier validation with invalid values."""
    # Test invalid network tier string
    with pytest.raises(ValueError, match='Invalid network_tier'):
        Resources(network_tier='invalid')

    # Test case insensitive validation
    r = Resources(network_tier='BEST')
    assert r.network_tier == resources_utils.NetworkTier.BEST

    r = Resources(network_tier='Standard')
    assert r.network_tier == resources_utils.NetworkTier.STANDARD


def test_network_tier_comparison():
    """Test network tier comparison in less_demanding_than method."""
    # Test network tier matching
    r1 = Resources(cloud=clouds.GCP(), network_tier='standard')
    r2 = Resources(cloud=clouds.GCP(), network_tier='standard')
    assert r1.less_demanding_than(r2)

    # Test standard <= best
    r1 = Resources(cloud=clouds.GCP(), network_tier='standard')
    r2 = Resources(cloud=clouds.GCP(), network_tier='best')
    assert r1.less_demanding_than(r2)

    # Test best not <= standard
    r1 = Resources(cloud=clouds.GCP(), network_tier='best')
    r2 = Resources(cloud=clouds.GCP(), network_tier='standard')
    assert not r1.less_demanding_than(r2)

    # Test None network_tier vs specified
    r1 = Resources(cloud=clouds.GCP(), network_tier='best')
    r2 = Resources(cloud=clouds.GCP())  # No network_tier specified
    assert not r1.less_demanding_than(r2)

    # Test None network_tier (should accept anything)
    r1 = Resources(cloud=clouds.GCP())  # No network_tier specified
    r2 = Resources(cloud=clouds.GCP(), network_tier='best')
    assert r1.less_demanding_than(r2)


def test_network_tier_cloud_features():
    """Test that network_tier=best requires CUSTOM_NETWORK_TIER feature."""
    # Test standard tier doesn't require custom network tier feature
    r = Resources(network_tier='standard')
    features = r.get_required_cloud_features()
    assert clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER not in features

    # Test best tier requires custom network tier feature
    r = Resources(network_tier='best')
    features = r.get_required_cloud_features()
    assert clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER in features


def test_network_tier_yaml_serialization():
    """Test network tier YAML serialization and deserialization."""
    # Test best network tier
    r = Resources(network_tier='best')
    yaml_config = r.to_yaml_config()
    assert yaml_config['network_tier'] == 'best'

    loaded_resources = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_resources.network_tier == resources_utils.NetworkTier.BEST

    # Test standard network tier
    r = Resources(network_tier='standard')
    yaml_config = r.to_yaml_config()
    assert yaml_config['network_tier'] == 'standard'

    loaded_resources = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_resources.network_tier == resources_utils.NetworkTier.STANDARD


def test_network_tier_with_gcp():
    """Test network tier functionality specifically with GCP cloud."""
    # Test GCP supports both standard and best network tiers
    r_standard = Resources(infra='gcp', network_tier='standard')
    r_standard.validate()
    assert r_standard.network_tier == resources_utils.NetworkTier.STANDARD

    r_best = Resources(infra='gcp', network_tier='best')
    r_best.validate()
    assert r_best.network_tier == resources_utils.NetworkTier.BEST


def test_network_tier_copy():
    """Test network tier preservation in copy operations."""
    r = Resources(network_tier='best', cpus=4)
    r_copy = r.copy()
    assert r_copy.network_tier == resources_utils.NetworkTier.BEST

    # Test overriding network tier in copy
    r_override = r.copy(network_tier='standard')
    assert r_override.network_tier == resources_utils.NetworkTier.STANDARD
    assert r_override.cpus == '4'  # Other properties preserved


def test_network_tier_repr():
    """Test that network tier appears in the string representation."""
    r = Resources(network_tier='best')
    repr_str = str(r)
    assert 'network_tier=best' in repr_str

    r = Resources(network_tier='standard')
    repr_str = str(r)
    assert 'network_tier=standard' in repr_str


def test_autostop_config():
    """Test autostop config override functionality."""
    # Override with down=True when no existing autostop config
    r = Resources()
    assert r.autostop_config is None

    r.override_autostop_config(down=True)
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is True
    assert r.autostop_config.idle_minutes == 0  # default value
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.JOBS_AND_SSH  # default value

    # Override with idle_minutes when no existing autostop config
    r = Resources()
    assert r.autostop_config is None

    r.override_autostop_config(idle_minutes=10)
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is False  # default value
    assert r.autostop_config.idle_minutes == 10
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.JOBS_AND_SSH  # default value

    # Override with both down and idle_minutes when no existing config
    r = Resources()
    assert r.autostop_config is None

    r.override_autostop_config(down=True,
                               idle_minutes=15,
                               wait_for=autostop_lib.AutostopWaitFor.JOBS)
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is True
    assert r.autostop_config.idle_minutes == 15
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.JOBS

    # Override when there's an existing autostop config
    r = Resources(autostop={
        'idle_minutes': 20,
        'down': False,
        'wait_for': 'none'
    })
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is False
    assert r.autostop_config.idle_minutes == 20
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE

    # Override only down flag
    r.override_autostop_config(down=True)
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is True
    assert r.autostop_config.idle_minutes == 20  # unchanged
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE  # unchanged

    # Override existing config with new idle_minutes
    r = Resources(autostop={'idle_minutes': 25, 'down': True})
    assert r.autostop_config.idle_minutes == 25
    assert r.autostop_config.down is True

    r.override_autostop_config(idle_minutes=30)
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is True  # unchanged
    assert r.autostop_config.idle_minutes == 30

    # Override existing config with all parameters
    r = Resources(autostop={
        'idle_minutes': 35,
        'down': False,
        'wait_for': 'jobs'
    })
    r.override_autostop_config(down=True,
                               idle_minutes=40,
                               wait_for=autostop_lib.AutostopWaitFor.NONE)
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is True
    assert r.autostop_config.idle_minutes == 40
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE

    # Call override with default parameters (should do nothing)
    r = Resources()
    assert r.autostop_config is None

    r.override_autostop_config()  # both parameters are default (False, None)
    assert r.autostop_config is None  # should remain None

    # Call override with default parameters on existing config
    r = Resources(autostop={
        'idle_minutes': 45,
        'down': True,
        'wait_for': 'none'
    })
    original_config = r.autostop_config

    r.override_autostop_config()  # should do nothing
    assert r.autostop_config is original_config  # same object
    assert r.autostop_config.idle_minutes == 45  # unchanged
    assert r.autostop_config.down is True  # unchanged
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE  # unchanged

    # Override with down=False (should still create config if none exists)
    r = Resources()
    assert r.autostop_config is None

    r.override_autostop_config(
        down=False,
        idle_minutes=50,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH)
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is True
    assert r.autostop_config.down is False
    assert r.autostop_config.idle_minutes == 50
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.JOBS_AND_SSH

    # Test with disabled autostop config
    r = Resources(autostop=False)
    assert r.autostop_config is not None
    assert r.autostop_config.enabled is False

    r.override_autostop_config(down=True,
                               idle_minutes=55,
                               wait_for=autostop_lib.AutostopWaitFor.NONE)
    assert r.autostop_config.enabled is False  # should remain disabled
    assert r.autostop_config.down is True
    assert r.autostop_config.idle_minutes == 55
    assert r.autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE


def test_disk_size_conversion():
    """Test disk size conversion to GB."""
    # Test integer input
    r = Resources(disk_size=100)
    assert r.disk_size == 100

    # Test string input with different units
    r = Resources(disk_size='100GB')
    assert r.disk_size == 100

    r = Resources(disk_size='1024MB')
    assert r.disk_size == 1

    r = Resources(disk_size='1TB')
    assert r.disk_size == 1024

    with pytest.raises(ValueError):
        Resources(disk_size='1024KB')

    with pytest.raises(ValueError):
        Resources(disk_size='1024B')

    r = Resources(disk_size='1PB')
    assert r.disk_size == 1024 * 1024

    # Test invalid format
    with pytest.raises(ValueError):
        Resources(disk_size='invalid')


def test_memory_conversion():
    """Test memory conversion to GB."""
    # Test integer input
    r = Resources(memory=8)
    assert r.memory == '8'

    # Test string input with different units
    r = Resources(memory='8GB')
    assert r.memory == '8.0'

    r = Resources(memory='8192MB')
    assert r.memory == '8.0'

    r = Resources(memory='1TB+')
    assert r.memory == '1024.0+'

    r = Resources(memory='1PB')
    assert r.memory == '1048576.0'

    # Test with plus suffix
    r = Resources(memory='8GB+')
    assert r.memory == '8.0+'

    # Test invalid format
    with pytest.raises(ValueError):
        Resources(memory='invalid')


def test_autostop_time_format():
    """Test autostop time format parsing."""
    # Test minutes format
    r = Resources(autostop='5m')
    assert r.autostop_config.idle_minutes == 5

    # Test hours format
    r = Resources(autostop='2h')
    assert r.autostop_config.idle_minutes == 120

    # Test days format
    r = Resources(autostop='1d')
    assert r.autostop_config.idle_minutes == 1440

    r = Resources(autostop=30)
    assert r.autostop_config.idle_minutes == 30

    # Test numeric format
    r = Resources(autostop='60')
    assert r.autostop_config.idle_minutes == 60

    # Test invalid format
    with pytest.raises(ValueError):
        Resources(autostop='invalid')


def test_priority_basic():
    """Test basic priority field functionality."""
    # Test with no priority specified (defaults to None)
    r = Resources()
    assert r.priority is None

    # Test with valid priority values
    r = Resources(priority=constants.MIN_PRIORITY)
    assert r.priority == constants.MIN_PRIORITY

    r = Resources(priority=constants.DEFAULT_PRIORITY)
    assert r.priority == constants.DEFAULT_PRIORITY

    r = Resources(priority=constants.MAX_PRIORITY)
    assert r.priority == constants.MAX_PRIORITY

    # Test with None explicitly
    r = Resources(priority=None)
    assert r.priority is None


def test_priority_validation():
    """Test priority field validation with invalid values."""
    # Test invalid priority - below range
    error_message = f'Priority must be between {constants.MIN_PRIORITY} and {constants.MAX_PRIORITY}'
    with pytest.raises(ValueError, match=error_message):
        Resources(priority=constants.MIN_PRIORITY - 1)

    # Test invalid priority - above range
    with pytest.raises(ValueError, match=error_message):
        Resources(priority=constants.MAX_PRIORITY + 1)


def test_priority_yaml_serialization():
    """Test priority YAML serialization and deserialization."""
    # Test priority serialization
    r = Resources(priority=750)
    yaml_config = r.to_yaml_config()
    assert yaml_config['priority'] == 750

    loaded_resources = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_resources.priority == 750

    # Test None priority (should not appear in yaml)
    r = Resources(priority=None)
    yaml_config = r.to_yaml_config()
    assert 'priority' not in yaml_config

    # Test priority with other fields
    r = Resources(cpus=4, memory='8', priority=200)
    yaml_config = r.to_yaml_config()
    assert yaml_config['priority'] == 200
    assert yaml_config['cpus'] == '4'
    assert yaml_config['memory'] == '8'

    loaded_resources = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_resources.priority == 200
    assert loaded_resources.cpus == '4'
    assert loaded_resources.memory == '8'


def test_priority_copy():
    """Test priority preservation in copy operations."""
    r = Resources(priority=300, cpus=4)
    r_copy = r.copy()
    assert r_copy.priority == 300

    # Test overriding priority in copy
    r_override = r.copy(priority=600)
    assert r_override.priority == 600
    assert r_override.cpus == '4'  # Other properties preserved

    # Test copying with None priority
    r_none = Resources(priority=None, cpus=2)
    r_copy = r_none.copy()
    assert r_copy.priority is None

    # Test overriding None priority in copy
    r_override = r_none.copy(priority=100)
    assert r_override.priority == 100
    assert r_override.cpus == '2'


def test_priority_with_any_of():
    """Test priority field works with any_of configuration."""
    config = {
        'priority': constants.DEFAULT_PRIORITY,  # Base priority
        'any_of': [
            {
                'cpus': 8,
                'memory': 16
            },
            {
                'cpus': 4,
                'memory': 32,
                'priority': 800  # Override priority
            },
        ]
    }
    resources_set = Resources.from_yaml_config(config)

    assert isinstance(resources_set, set)
    assert len(resources_set) == 2

    resources_list = list(resources_set)

    # Find resources by properties
    r_cpus8 = next((r for r in resources_list if r.cpus == '8'), None)
    r_cpus4 = next((r for r in resources_list if r.cpus == '4'), None)

    assert r_cpus8 is not None
    assert r_cpus8.priority == constants.DEFAULT_PRIORITY  # Base priority

    assert r_cpus4 is not None
    assert r_cpus4.priority == 800  # Override priority


def test_priority_with_ordered():
    """Test priority field works with ordered configuration."""
    config = {
        'priority': 300,  # Base priority
        'ordered': [
            {
                'infra': 'gcp',
                'accelerators': 'A100:8'
            },
            {
                'infra': 'aws',
                'accelerators': 'V100:8',
                'priority': 700  # Override priority
            },
        ]
    }
    resources_list = Resources.from_yaml_config(config)

    assert isinstance(resources_list, list)
    assert len(resources_list) == 2

    # Ordered resources should preserve order
    assert resources_list[0].infra.cloud.lower() == 'gcp'
    assert resources_list[0].priority == 300  # Base priority

    assert resources_list[1].infra.cloud.lower() == 'aws'
    assert resources_list[1].priority == 700  # Override priority


@pytest.mark.parametrize(['resources_kwargs', 'expected_yaml_config'], [
    ({
        'infra': 'aws/us-east-1',
        'accelerators': 'A10',
        'priority': 400
    }, {
        'infra': 'aws/us-east-1',
        'accelerators': {
            'A10': 1
        },
        'priority': 400,
        'disk_size': 256,
    }),
    ({
        'cpus': 4,
        'memory': '8+',
        'priority': 0
    }, {
        'cpus': '4',
        'memory': '8+',
        'priority': 0,
        'disk_size': 256,
    }),
    ({
        'cpus': 2,
        'priority': 1000
    }, {
        'cpus': '2',
        'priority': 1000,
        'disk_size': 256,
    }),
])
def test_priority_to_yaml_and_load(resources_kwargs, expected_yaml_config):
    """Test priority field in to_yaml_config and from_yaml_config."""
    r = Resources(**resources_kwargs)
    yaml_config = r.to_yaml_config()
    assert yaml_config == expected_yaml_config

    loaded_r = list(Resources.from_yaml_config(yaml_config))[0]
    assert loaded_r.priority == r.priority
    assert loaded_r.cpus == r.cpus
    if 'accelerators' in expected_yaml_config:
        assert loaded_r.accelerators == r.accelerators


@pytest.mark.parametrize(
    'r_kwargs, blocked_kwargs, expected',
    [
        # All fields match
        ({
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': True
        }, {
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': True
        }, True),
        # use_spot mismatch
        ({
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': True
        }, {
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': False
        }, False),
        # cloud mismatch
        ({
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
        }, {
            'cloud': clouds.GCP(),
            'instance_type': 'g2-standard-4',
            'region': 'us-east4',
            'zone': 'us-east4-c',
            'accelerators': {
                'L4': 1
            },
        }, False),
        # instance_type mismatch
        ({
            'cloud': clouds.AWS(),
            'instance_type': 'p3.2xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': True
        }, {
            'cloud': clouds.AWS(),
            'instance_type': 'p3.8xlarge',
            'region': 'us-west-2',
            'zone': 'us-west-2a',
            'accelerators': {
                'V100': 1
            },
            'use_spot': True
        }, False),
    ])
def test_should_be_blocked_by(r_kwargs, blocked_kwargs, expected):
    """Test should_be_blocked_by method."""
    r = Resources(**r_kwargs)
    blocked = Resources(**blocked_kwargs)
    assert r.should_be_blocked_by(blocked) == expected
