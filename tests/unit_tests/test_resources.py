from typing import Dict
from unittest.mock import Mock

import pytest

from sky import clouds
from sky.resources import Resources

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
