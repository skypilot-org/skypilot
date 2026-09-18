"""Tests for the OCI cloud."""
from unittest import mock

import pytest

from sky.clouds import oci as oci_cloud


@pytest.mark.parametrize(
    'accelerators,expected_tag',
    [
        (None, 'skypilot:cpu-ubuntu-2204'),
        ({
            'H100': 8
        }, 'skypilot:gpu-ubuntu-2204'),
        ({
            'B200': 8
        }, 'skypilot:gpu-ubuntu-2204'),
        ({
            'GB200': 4
        }, 'skypilot:gpu-ubuntu-2204'),
        ({
            'RTXPRO6000': 8
        }, 'skypilot:gpu-ubuntu-2204'),
        # The default GPU image is an NVIDIA Marketplace listing; AMD Instinct
        # shapes fall back to the plain OS image.
        ({
            'MI300X': 8
        }, 'skypilot:cpu-ubuntu-2204'),
        ({
            'MI355X': 8
        }, 'skypilot:cpu-ubuntu-2204'),
    ])
def test_default_image_tag_depends_on_gpu_vendor(accelerators, expected_tag):
    cloud = oci_cloud.OCI()
    with mock.patch.object(oci_cloud.OCI,
                           'get_accelerators_from_instance_type',
                           return_value=accelerators):
        # pylint: disable=protected-access
        assert cloud._get_default_image_tag('any-shape') == expected_tag
