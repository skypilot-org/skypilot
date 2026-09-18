"""Tests for the OCI catalog's subscription-filtered view."""
# pylint: disable=protected-access
import os
import time
from unittest import mock

import pytest

from sky.catalog import common as catalog_common
from sky.catalog import oci_catalog
from sky.utils import annotations

_HEADER = ('InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,'
           'GpuInfo,Price,SpotPrice,Region,AvailabilityZone\n')
_H100_ASHBURN = ('BM.GPU.H100.8,H100,8,224,2048,,80,,'
                 'us-ashburn-1,us-ashburn-1-AD-1\n')
_H100_FRANKFURT = ('BM.GPU.H100.8,H100,8,224,2048,,80,,'
                   'eu-frankfurt-1,eu-frankfurt-1-AD-1\n')
_B200_ASHBURN = ('BM.GPU.B200.8,B200,8,256,4096,,112,,'
                 'us-ashburn-1,us-ashburn-1-AD-1\n')

CATALOG_V1 = _HEADER + _H100_ASHBURN + _H100_FRANKFURT
CATALOG_V2 = CATALOG_V1 + _B200_ASHBURN

_PULL_FREQUENCY_HOURS = 1


class _Response:

    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


@pytest.fixture
def isolated_catalog(tmp_path, monkeypatch):
    """Points the OCI catalog module at a fresh, empty catalog directory.

    Yields the on-disk path of oci/vms.csv inside that directory.
    """
    monkeypatch.setattr(catalog_common, '_ABSOLUTE_VERSIONED_CATALOG_DIR',
                        str(tmp_path))
    monkeypatch.setattr(
        oci_catalog, '_vms_df',
        catalog_common.read_catalog(oci_catalog._VMS_FILE,
                                    pull_frequency_hours=_PULL_FREQUENCY_HOURS))
    monkeypatch.setattr(oci_catalog, '_df', None)
    monkeypatch.setattr(oci_catalog, '_df_catalog_mtime', None)
    monkeypatch.setattr(oci_catalog, '_subscribed_regions', None)
    annotations.clear_request_level_cache()
    yield catalog_common.get_catalog_path(oci_catalog._VMS_FILE)
    annotations.clear_request_level_cache()


def _make_stale(catalog_path: str) -> None:
    stale = time.time() - 2 * _PULL_FREQUENCY_HOURS * 3600
    os.utime(catalog_path, (stale, stale))


def test_filtered_view_follows_catalog_refresh(isolated_catalog):
    """A long-running server must see new catalog rows after a re-pull."""
    catalog_path = isolated_catalog
    subscribed = mock.Mock(return_value=['us-ashburn-1'])
    with mock.patch.object(oci_catalog, '_fetch_subscribed_regions',
                           subscribed), \
         mock.patch.object(catalog_common.requests, 'get',
                           return_value=_Response(CATALOG_V1)) as get:
        # First call downloads the catalog and filters it to the tenancy's
        # subscribed regions.
        df = oci_catalog._get_df()
        assert sorted(df['InstanceType']) == ['BM.GPU.H100.8']
        assert set(df['Region']) == {'us-ashburn-1'}
        assert get.call_count == 1
        assert subscribed.call_count == 1
        assert not oci_catalog.instance_type_exists('BM.GPU.B200.8')

        # Later requests with an unchanged catalog reuse the view and do not
        # call OCI again.
        assert oci_catalog._get_df() is df
        annotations.clear_request_level_cache()
        assert oci_catalog._get_df() is df
        assert get.call_count == 1
        assert subscribed.call_count == 1

        # The scheduled refresh: the local copy is older than the pull
        # frequency and upstream now serves a catalog with a new shape.
        get.return_value = _Response(CATALOG_V2)
        _make_stale(catalog_path)
        annotations.clear_request_level_cache()

        new_df = oci_catalog._get_df()
        assert new_df is not df
        assert sorted(
            new_df['InstanceType']) == ['BM.GPU.B200.8', 'BM.GPU.H100.8']
        assert set(new_df['Region']) == {'us-ashburn-1'}
        assert oci_catalog.instance_type_exists('BM.GPU.B200.8')
        assert oci_catalog.get_hourly_cost('BM.GPU.B200.8') == 112
        assert get.call_count == 2
        # The subscription lookup is repeated only because the catalog
        # changed.
        assert subscribed.call_count == 2


def test_unfiltered_view_is_the_lazy_frame(isolated_catalog):
    """Without subscription info the self-refreshing frame is served as is."""
    del isolated_catalog
    with mock.patch.object(oci_catalog, '_fetch_subscribed_regions',
                           return_value=[]), \
         mock.patch.object(catalog_common.requests, 'get',
                           return_value=_Response(CATALOG_V1)):
        df = oci_catalog._get_df()
        assert df is oci_catalog._vms_df
        assert set(df['Region']) == {'us-ashburn-1', 'eu-frankfurt-1'}
        assert oci_catalog._get_df() is df


def test_fresh_catalog_does_not_repeat_subscription_lookup(isolated_catalog):
    """Clearing the request cache alone must not trigger OCI API calls."""
    del isolated_catalog
    subscribed = mock.Mock(return_value=['eu-frankfurt-1'])
    with mock.patch.object(oci_catalog, '_fetch_subscribed_regions',
                           subscribed), \
         mock.patch.object(catalog_common.requests, 'get',
                           return_value=_Response(CATALOG_V1)) as get:
        first = oci_catalog._get_df()
        for _ in range(3):
            annotations.clear_request_level_cache()
            assert oci_catalog._get_df() is first
        assert set(first['Region']) == {'eu-frankfurt-1'}
        assert get.call_count == 1
        assert subscribed.call_count == 1
