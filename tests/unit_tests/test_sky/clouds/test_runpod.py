"""Tests for RunPod cloud candidate filtering."""

from unittest import mock

from sky.clouds import cloud
from sky.clouds import runpod


def _catalog_regions():
    nl = cloud.Region('NL')
    nl.set_zones([cloud.Zone('EU-NL-1'), cloud.Zone('EU-NL-2')])
    ro = cloud.Region('RO')
    ro.set_zones([cloud.Zone('EU-RO-1')])
    return [nl, ro]


def test_regions_with_offering_uses_catalog_without_live_capacity():
    """Optimizer region discovery remains independent of live RunPod stock."""
    with mock.patch(
            'sky.clouds.runpod.catalog.get_region_zones_for_instance_type',
            return_value=_catalog_regions()) as catalog_lookup, mock.patch(
                'sky.adaptors.runpod.'
                'available_data_center_ids_for_instance_type') as capacity:
        regions = runpod.RunPod.regions_with_offering(
            instance_type='1x_A40_SECURE',
            accelerators=None,
            use_spot=True,
            region=None,
            zone=None,
            resources=None)

    catalog_lookup.assert_called_once()
    capacity.assert_not_called()
    assert [(region.name, [zone.name
                           for zone in region.zones])
            for region in regions] == [('NL', ['EU-NL-1', 'EU-NL-2']),
                                       ('RO', ['EU-RO-1'])]


def test_zones_provision_loop_filters_using_live_capacity():
    """Provisioning skips catalog zones with no current RunPod capacity."""
    nl = cloud.Region('NL')
    nl.set_zones([cloud.Zone('EU-NL-1'), cloud.Zone('EU-NL-2')])
    ro = cloud.Region('RO')
    ro.set_zones([cloud.Zone('EU-RO-1')])

    with mock.patch(
            'sky.clouds.runpod.catalog.get_region_zones_for_instance_type',
            return_value=[nl, ro]), mock.patch(
                'sky.adaptors.runpod.'
                'available_data_center_ids_for_instance_type',
                return_value={'EU-NL-2'}):
        zones = list(
            runpod.RunPod.zones_provision_loop(region='NL',
                                               num_nodes=1,
                                               instance_type='1x_A40_SECURE',
                                               accelerators=None,
                                               use_spot=True))

    assert [[zone.name for zone in zone_list] for zone_list in zones
           ] == [['EU-NL-2']]
