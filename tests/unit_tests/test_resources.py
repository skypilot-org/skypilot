import importlib
import os
from typing import Dict
from unittest import mock

import pytest

from sky import clouds
from sky import global_user_state
from sky import skypilot_config
from sky.resources import Resources
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
    assert r_allowed.labels == allowed_labels, ('Allowed labels '
                                                'should be the same')

    # Check for each invalid label
    for invalid_label, value in invalid_labels.items():
        l = {invalid_label: value}
        with pytest.raises(ValueError):
            _ = Resources(cloud=cloud, labels=l)
            assert False, (f'Resources were initialized with '
                           f'invalid label {invalid_label}={value}')


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
    global_user_state.set_enabled_clouds(['aws', 'gcp'])
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
    global_user_state.set_enabled_clouds(['aws'])
    allowed_labels = {
        **GLOBAL_VALID_LABELS,
        'domain/key': 'value',  # Valid for AWS
    }
    invalid_labels = {
        **GLOBAL_INVALID_LABELS,
        'aws:cannotstartwithaws': 'value',
    }
    _run_label_test(allowed_labels, invalid_labels)


@mock.patch('sky.clouds.service_catalog.instance_type_exists',
            return_value=True)
@mock.patch('sky.clouds.service_catalog.get_accelerators_from_instance_type',
            return_value={'fake-acc': 2})
@mock.patch('sky.clouds.service_catalog.get_image_id_from_tag',
            return_value='fake-image')
@mock.patch.object(clouds.aws, 'DEFAULT_SECURITY_GROUP_NAME', 'fake-default-sg')
def test_aws_make_deploy_variables(*mocks) -> None:
    os.environ['SKYPILOT_CONFIG'] = './tests/test_yamls/test_aws_config.yaml'
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
                                            dryrun=True)
    assert config == expected_config, ('unexpected resource '
                                       'variables generated')
