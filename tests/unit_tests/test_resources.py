from typing import Dict
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from apex import clouds
from apex import apex_config
from apex.resources import Resources
from apex.utils import resources_utils

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
    mock = Mock()
    r = Resources(cloud=mock, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r.get_reservations_available_resources()
    mock.get_reservations_available_resources.assert_called_once_with(
        "instance_type", "region", "zone", set())


def _run_label_test(allowed_labels: Dict[str, str],
                    invalid_labels: Dict[str, str], cloud: clouds.Cloud):
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


@patch.object(apex_config, 'CONFIG_PATH',
              './tests/test_yamls/test_aws_config.yaml')
@patch.object(apex_config, '_dict', None)
@patch.object(apex_config, '_loaded_config_path', None)
@patch('sky.clouds.service_catalog.instance_type_exists', return_value=True)
@patch('sky.clouds.service_catalog.get_accelerators_from_instance_type',
       return_value={'fake-acc': 2})
@patch('sky.clouds.service_catalog.get_image_id_from_tag',
       return_value='fake-image')
@patch.object(clouds.aws, 'DEFAULT_SECURITY_GROUP_NAME', 'fake-default-sg')
def test_aws_make_deploy_variables(*mocks) -> None:
    apex_config._try_load_config()

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
        'custom_disk_perf': True,
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
