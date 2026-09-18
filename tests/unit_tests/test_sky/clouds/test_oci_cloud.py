"""Tests for the OCI cloud."""
# pylint: disable=protected-access
from unittest import mock

import pytest

from sky import exceptions
from sky.clouds import oci as oci_cloud

_CPU_TAG = 'skypilot:cpu-ubuntu-2204'
_GPU_TAG = 'skypilot:gpu-ubuntu-2204'
_REGION = 'us-ashburn-1'


@pytest.mark.parametrize('instance_type,expected', [
    ('BM.GPU.GB200.4', True),
    ('BM.GPU.GB300.4', True),
    ('VM.Standard.A1.Flex', True),
    ('VM.Standard.A1.Flex$_4_24', True),
    ('BM.Standard.A1.160', True),
    ('VM.Standard.A2.Flex', True),
    ('VM.Standard.A4.Flex', True),
    ('VM.Standard.A4.Ax.Flex', True),
    ('BM.Standard.A4.48', True),
    ('BM.GPU.B200.8', False),
    ('BM.GPU.B300.8', False),
    ('BM.GPU.H100.8', False),
    ('BM.GPU.A10.4', False),
    ('VM.GPU.A10.1', False),
    ('BM.GPU.A100-v2.8', False),
    ('BM.GPU.MI300X.8', False),
    ('VM.Standard.E4.Flex$_2_8', False),
    ('VM.Standard3.Flex', False),
])
def test_is_arm_shape(instance_type, expected):
    assert oci_cloud._is_arm_shape(instance_type) is expected


def _default_image_tag(instance_type, accelerators, arm_image_in_catalog):
    cloud = oci_cloud.OCI()
    with mock.patch.object(oci_cloud.OCI,
                           'get_accelerators_from_instance_type',
                           return_value=accelerators), \
         mock.patch.object(oci_cloud.catalog,
                           'is_image_tag_valid',
                           return_value=arm_image_in_catalog) as is_valid:
        tag = cloud._get_default_image_tag(instance_type, _REGION)
    return tag, is_valid


@pytest.mark.parametrize(
    'instance_type,accelerators,expected_tag',
    [
        ('VM.Standard.E4.Flex$_8_32', None, _CPU_TAG),
        ('BM.GPU.H100.8', {
            'H100': 8
        }, _GPU_TAG),
        ('BM.GPU.B200.8', {
            'B200': 8
        }, _GPU_TAG),
        ('BM.GPU.RTXPRO.8', {
            'RTXPRO6000': 8
        }, _GPU_TAG),
        # The default GPU image is an NVIDIA Marketplace listing; AMD Instinct
        # shapes fall back to the plain OS image.
        ('BM.GPU.MI300X.8', {
            'MI300X': 8
        }, _CPU_TAG),
        ('BM.GPU.MI355X.8', {
            'MI355X': 8
        }, _CPU_TAG),
    ])
def test_default_image_tag_for_x86_shapes(instance_type, accelerators,
                                          expected_tag):
    tag, is_valid = _default_image_tag(instance_type,
                                       accelerators,
                                       arm_image_in_catalog=False)
    assert tag == expected_tag
    # x86 shapes never consult the catalog for an Arm image.
    is_valid.assert_not_called()


@pytest.mark.parametrize('instance_type,accelerators', [
    ('BM.GPU.GB200.4', {
        'GB200': 4
    }),
    ('BM.GPU.GB300.4', {
        'GB300': 4
    }),
    ('VM.Standard.A1.Flex$_4_24', None),
])
def test_arm_shape_without_arm_image_is_an_actionable_error(
        instance_type, accelerators):
    # Grace/Ampere hosts cannot boot the x86-64 default images, and
    # oci/images.csv has no aarch64 image yet: refuse instead of silently
    # picking an image that will never boot, and tell the user what to set.
    with pytest.raises(exceptions.ResourcesUnavailableError) as exc_info:
        _default_image_tag(instance_type,
                           accelerators,
                           arm_image_in_catalog=False)
    # The image catalog is region-independent, so failing over is pointless.
    assert exc_info.value.no_failover
    message = str(exc_info.value)
    assert instance_type in message
    assert 'aarch64' in message
    assert 'image_id' in message


@pytest.mark.parametrize('instance_type,accelerators,expected_tag', [
    ('BM.GPU.GB200.4', {
        'GB200': 4
    }, oci_cloud._ARM_GPU_IMAGE_TAG),
    ('BM.GPU.GB300.4', {
        'GB300': 4
    }, oci_cloud._ARM_GPU_IMAGE_TAG),
    ('VM.Standard.A1.Flex$_4_24', None, oci_cloud._ARM_CPU_IMAGE_TAG),
])
def test_arm_shape_uses_arm_image_once_catalogued(instance_type, accelerators,
                                                  expected_tag):
    tag, is_valid = _default_image_tag(instance_type,
                                       accelerators,
                                       arm_image_in_catalog=True)
    assert tag == expected_tag
    is_valid.assert_called_once_with(expected_tag, _REGION, clouds='oci')
