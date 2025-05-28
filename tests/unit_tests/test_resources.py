import importlib
import os
from typing import Dict
from unittest import mock

import pytest

from sky import clouds
from sky import global_user_state
from sky import skypilot_config
from sky.clouds import cloud as sky_cloud
from sky.resources import Resources
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
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
        'domain/key': 'value',  # Valid for AWS
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'aws:cannotstartwithaws': 'value',
    }
    _run_label_test(allowed_labels, invalid_labels, cloud=clouds.AWS())


@mock.patch('sky.clouds.service_catalog.instance_type_exists',
            return_value=True)
@mock.patch('sky.clouds.service_catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.clouds.service_catalog.get_image_id_from_tag',
            return_value='fake-image')
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
