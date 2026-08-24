"""Tests for the Lium cloud."""
import pytest

from sky import clouds
from sky.clouds import lium
from sky.provision.lium import lium_utils


def test_lium_cloud():
    cloud = lium.Lium()
    assert cloud.canonical_name() == 'lium'
    assert repr(cloud) == 'Lium'


def test_lium_unsupported_features():
    cloud = lium.Lium()
    for feature, reason in cloud._CLOUD_UNSUPPORTED_FEATURES.items():
        assert feature in clouds.CloudImplementationFeatures
        assert isinstance(reason, str)


def test_lium_rejects_zones():
    cloud = lium.Lium()
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('US', 'zone-1')


def test_instance_type_round_trip():
    instance_type = lium_utils.make_instance_type('H100', 8)
    assert instance_type == 'H100_8x'
    assert lium_utils.parse_instance_type(instance_type) == ('H100', 8)


def test_instance_type_keeps_the_dash_in_an_accelerator_name():
    assert lium_utils.parse_instance_type('RTXPRO6000-WK_1x') == (
        'RTXPRO6000-WK', 1)


def test_parse_instance_type_rejects_a_malformed_name():
    with pytest.raises(ValueError):
        lium_utils.parse_instance_type('H100')


def test_accelerator_name_ignores_the_vendor_prefix():
    # The node API and the public node feed name the same GPU differently.
    assert lium_utils.accelerator_name('NVIDIA GeForce RTX 5090') == 'RTX5090'
    assert lium_utils.accelerator_name('RTX 5090') == 'RTX5090'
    assert lium_utils.accelerator_name('NVIDIA H100 80GB HBM3') == 'H100-SXM'


def test_accelerator_name_skips_an_unknown_model():
    assert lium_utils.accelerator_name('Some New GPU') is None
