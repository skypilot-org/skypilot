"""Tests for the OCI catalog's subscription-filtered view."""
# pylint: disable=protected-access
import os
import time
from unittest import mock

import pandas as pd
import pytest

from sky.adaptors import oci as oci_adaptor
from sky.catalog import common as catalog_common
from sky.catalog import oci_catalog
from sky.clouds.utils import oci_utils
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


def test_refresh_by_another_worker_is_picked_up(isolated_catalog):
    """A refresh done by another server worker must reach this one too.

    The API server runs several worker processes. Only one of them
    downloads the new catalog; the others see a fresh file on disk and
    must still re-read it instead of serving the copy they loaded before.
    """
    catalog_path = isolated_catalog
    with mock.patch.object(oci_catalog, '_fetch_subscribed_regions',
                           return_value=['us-ashburn-1']), \
         mock.patch.object(catalog_common.requests, 'get',
                           return_value=_Response(CATALOG_V1)) as get:
        assert sorted(
            oci_catalog._get_df()['InstanceType']) == ['BM.GPU.H100.8']

        # Another worker, with its own lazy frame for the same file, finds
        # the catalog stale and downloads the new one.
        get.return_value = _Response(CATALOG_V2)
        _make_stale(catalog_path)
        other_worker = catalog_common.read_catalog(
            oci_catalog._VMS_FILE, pull_frequency_hours=_PULL_FREQUENCY_HOURS)
        assert 'BM.GPU.B200.8' in set(other_worker['InstanceType'])
        assert get.call_count == 2

        # This worker's next request sees the new rows without downloading.
        annotations.clear_request_level_cache()
        assert sorted(oci_catalog._get_df()['InstanceType']) == [
            'BM.GPU.B200.8', 'BM.GPU.H100.8'
        ]
        assert oci_catalog.instance_type_exists('BM.GPU.B200.8')
        assert get.call_count == 2


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


def test_expired_token_serves_full_catalog_until_a_new_session(
        isolated_catalog, monkeypatch):
    """An expired session token must not pin the unfiltered view."""
    del isolated_catalog
    pytest.importorskip('oci')
    # The token is expired for the first two catalog queries; the user then
    # runs `oci session authenticate` and the third one succeeds.
    subscription = mock.MagicMock()
    subscription.region_name = 'us-ashburn-1'
    client = mock.MagicMock()
    client.list_region_subscriptions.return_value.data = [subscription]
    expired = oci_adaptor.OCISessionTokenError('token expired')
    get_identity_client = mock.MagicMock(side_effect=[expired, expired, client])
    monkeypatch.setattr(oci_adaptor, 'get_identity_client', get_identity_client)
    monkeypatch.setattr(
        oci_adaptor,
        'get_oci_config',
        lambda region=None, profile='DEFAULT': {'tenancy': 'ocid1.tenancy.x'})
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    monkeypatch.setattr(oci_catalog, '_subscription_lookup_warned', False)
    monkeypatch.setattr(oci_catalog, 'logger', mock.MagicMock())
    with mock.patch.object(catalog_common.requests,
                           'get',
                           return_value=_Response(CATALOG_V1)):
        both = {'us-ashburn-1', 'eu-frankfurt-1'}
        assert set(oci_catalog._get_df()['Region']) == both
        # Not cached: the next query retries the lookup instead of serving
        # the unfiltered catalog until the process restarts.
        assert oci_catalog._df is None
        assert set(oci_catalog._get_df()['Region']) == both
        # Reported once, not on every query.
        oci_catalog.logger.warning.assert_called_once_with('token expired')
        oci_catalog.logger.debug.assert_called_once_with('token expired')

        filtered = oci_catalog._get_df()
        assert set(filtered['Region']) == {'us-ashburn-1'}
        assert oci_catalog._df is filtered
        # Once resolved, the lookup is not repeated.
        assert oci_catalog._get_df() is filtered
        assert get_identity_client.call_count == 3


# ---------------------------------------------------------------------------
# Image lookups
# ---------------------------------------------------------------------------

_CPU_TAG = 'skypilot:cpu-ubuntu-2204'
_GPU_TAG = 'skypilot:gpu-ubuntu-2204'
_IMAGES = pd.DataFrame([
    {
        'Tag': _CPU_TAG,
        'Region': 'us-ashburn-1',
        'OS': 'ubuntu',
        'OSVersion': '22.04',
        'ImageId': 'ocid1.image.oc1.iad.aaaa',
        'AppCatalogListingId': None,
        'ResourceVersion': None,
    },
    {
        # Marketplace images are region neutral and catalogued once.
        'Tag': _GPU_TAG,
        'Region': None,
        'OS': 'ubuntu',
        'OSVersion': '22.04',
        'ImageId': 'ocid1.image.oc1..bbbb',
        'AppCatalogListingId': 'ocid1.appcataloglisting.oc1..cccc',
        'ResourceVersion': '24.05.1',
    },
])


@pytest.fixture(name='image_catalog')
def fixture_image_catalog(monkeypatch):
    monkeypatch.setattr(oci_catalog, '_image_df', _IMAGES.copy())


@pytest.mark.usefixtures('image_catalog')
def test_image_id_for_a_region_with_a_platform_image():
    image = oci_catalog.get_image_id_from_tag(_CPU_TAG, 'us-ashburn-1')
    assert image.split(oci_utils.oci_config.IMAGE_TAG_SPERATOR)[0] == (
        'ocid1.image.oc1.iad.aaaa')


@pytest.mark.usefixtures('image_catalog')
def test_marketplace_image_serves_every_region():
    image = oci_catalog.get_image_id_from_tag(_GPU_TAG, 'af-casablanca-1')
    assert image == '|'.join([
        'ocid1.image.oc1..bbbb', 'ocid1.appcataloglisting.oc1..cccc', '24.05.1'
    ])


@pytest.mark.usefixtures('image_catalog')
def test_no_image_for_a_region_without_a_platform_image_row():
    # Neither a row for the region nor a region-neutral one: None, rather
    # than another region's image or a formatted 'None|...' string.
    assert oci_catalog.get_image_id_from_tag(_CPU_TAG,
                                             'af-casablanca-1') is None


@pytest.mark.usefixtures('image_catalog')
def test_no_image_for_an_unknown_tag():
    assert oci_catalog.get_image_id_from_tag('skypilot:no-such-tag',
                                             'us-ashburn-1') is None
