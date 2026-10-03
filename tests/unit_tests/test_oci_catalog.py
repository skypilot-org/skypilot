"""Tests for the OCI catalog's subscription-filtered view."""
# pylint: disable=protected-access
from unittest import mock

import pandas as pd
import pytest

from sky.adaptors import oci as oci_adaptor
from sky.catalog import oci_catalog
from sky.clouds.utils import oci_utils

_TENANCY = 'ocid1.tenancy.oc1..aaaaaaaatest'

_CATALOG = pd.DataFrame({
    'InstanceType': ['BM.GPU.H100.8', 'BM.GPU.H100.8'],
    'AcceleratorName': ['H100', 'H100'],
    'AcceleratorCount': [8, 8],
    'vCPUs': [224, 224],
    'MemoryGiB': [2048, 2048],
    'GpuInfo': [None, None],
    'Price': [80.0, 80.0],
    'SpotPrice': [None, None],
    'Region': ['us-ashburn-1', 'eu-frankfurt-1'],
    'AvailabilityZone': ['US-ASHBURN-AD-1', 'EU-FRANKFURT-1-AD-1'],
})


def _subscription(region_name):
    subscription = mock.MagicMock()
    subscription.region_name = region_name
    return subscription


@pytest.fixture(name='catalog')
def fixture_catalog(monkeypatch):
    """A fresh, in-memory catalog with the subscription lookup stubbed."""
    pytest.importorskip('oci')
    monkeypatch.setattr(oci_catalog, '_df', None)
    monkeypatch.setattr(oci_catalog, '_subscription_lookup_warned', False)
    monkeypatch.setattr(oci_catalog.common, 'read_catalog',
                        lambda filename: _CATALOG.copy())
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    monkeypatch.setattr(
        oci_adaptor,
        'get_oci_config',
        lambda region=None, profile='DEFAULT': {'tenancy': _TENANCY})
    monkeypatch.setattr(oci_catalog, 'logger', mock.MagicMock())
    return monkeypatch


def test_subscribed_regions_filter_is_cached(catalog):
    client = mock.MagicMock()
    client.list_region_subscriptions.return_value.data = [
        _subscription('us-ashburn-1')
    ]
    get_identity_client = mock.MagicMock(return_value=client)
    catalog.setattr(oci_adaptor, 'get_identity_client', get_identity_client)

    df = oci_catalog._get_df()

    assert set(df['Region']) == {'us-ashburn-1'}
    assert oci_catalog._get_df() is df
    assert get_identity_client.call_count == 1


def test_expired_token_serves_full_catalog_until_a_new_session(catalog):
    # The token is expired for the first two catalog queries; the user then
    # runs `oci session authenticate` and the third one succeeds.
    client = mock.MagicMock()
    client.list_region_subscriptions.return_value.data = [
        _subscription('us-ashburn-1')
    ]
    expired = oci_adaptor.OCISessionTokenError('token expired')
    get_identity_client = mock.MagicMock(side_effect=[expired, expired, client])
    catalog.setattr(oci_adaptor, 'get_identity_client', get_identity_client)

    unfiltered = oci_catalog._get_df()
    assert set(unfiltered['Region']) == {'us-ashburn-1', 'eu-frankfurt-1'}
    # Not cached: the next query retries the lookup instead of serving the
    # unfiltered catalog until the process restarts.
    assert oci_catalog._df is None
    assert set(
        oci_catalog._get_df()['Region']) == {'us-ashburn-1', 'eu-frankfurt-1'}
    # Reported once, not on every query.
    oci_catalog.logger.warning.assert_called_once_with('token expired')
    oci_catalog.logger.debug.assert_called_once_with('token expired')

    filtered = oci_catalog._get_df()
    assert set(filtered['Region']) == {'us-ashburn-1'}
    assert oci_catalog._df is filtered
    assert get_identity_client.call_count == 3
    # Once resolved, the lookup is not repeated.
    assert oci_catalog._get_df() is filtered
    assert get_identity_client.call_count == 3
