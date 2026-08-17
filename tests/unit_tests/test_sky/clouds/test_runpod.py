"""Tests for RunPod cloud candidate filtering."""

from unittest import mock

from sky.clouds import cloud
from sky.clouds import runpod


def test_regions_with_offering_filters_zones_using_live_capacity():
    """Exclude catalog zones that RunPod no longer reports as available."""
    nl = cloud.Region('NL')
    nl.set_zones([cloud.Zone('EU-NL-1'), cloud.Zone('EU-NL-2')])
    ro = cloud.Region('RO')
    ro.set_zones([cloud.Zone('EU-RO-1')])

    with mock.patch(
            'sky.clouds.runpod.catalog.get_region_zones_for_instance_type',
            return_value=[nl, ro]), mock.patch(
                'sky.provision.runpod.utils.'
                'available_data_center_ids_for_instance_type',
                return_value={'EU-NL-2'}):
        regions = runpod.RunPod.regions_with_offering(
            instance_type='1x_A40_SECURE',
            accelerators=None,
            use_spot=True,
            region=None,
            zone=None,
            resources=None)

    assert [(region.name, [zone.name
                           for zone in region.zones])
            for region in regions] == [('NL', ['EU-NL-2'])]
