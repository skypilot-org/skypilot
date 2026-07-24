"""Tests for the Nebius personalized pricing catalog cache."""

# pylint: disable=protected-access
import threading
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import nebius_catalog


def _catalog(price: float) -> pd.DataFrame:
    return pd.DataFrame([{
        'InstanceType': 'test-instance',
        'Region': 'eu-north1',
        'Price': price,
    }])


@pytest.fixture(autouse=True)
def _reset_personal_catalog_cache():
    nebius_catalog._personal_catalogs.clear()
    nebius_catalog._personal_catalog_locks.clear()
    yield
    nebius_catalog._personal_catalogs.clear()
    nebius_catalog._personal_catalog_locks.clear()


def test_personal_catalog_failure_is_cached():
    clock = [1000.0]
    static_df = _catalog(1.0)

    def slow_failed_fetch():
        # Simulate a request that takes longer than the retry interval.
        clock[0] += nebius_catalog._PERSONAL_PRICING_RETRY_SECONDS + 1
        raise RuntimeError('Nebius is unavailable')

    fetch = mock.Mock(side_effect=slow_failed_fetch)

    with mock.patch.object(nebius_catalog, '_static_df', static_df), \
         mock.patch.object(nebius_catalog.skypilot_config,
                           'get_nested',
                           return_value=True), \
         mock.patch('sky.adaptors.nebius.get_tenant_id',
                    return_value='tenant-a'), \
         mock.patch.object(nebius_catalog.time,
                           'time',
                           side_effect=lambda: clock[0]), \
         mock.patch.object(nebius_catalog, '_fetch_user_catalog', fetch), \
         mock.patch.object(nebius_catalog, 'logger'):
        assert nebius_catalog._get_df() is static_df

        # The retry interval starts when the slow failure finishes, so an
        # immediate lookup must use the failure cache.
        assert nebius_catalog._get_df() is static_df
        assert fetch.call_count == 1

        clock[0] += nebius_catalog._PERSONAL_PRICING_RETRY_SECONDS - 1
        assert nebius_catalog._get_df() is static_df
        assert fetch.call_count == 1

        clock[0] += 2
        assert nebius_catalog._get_df() is static_df
        assert fetch.call_count == 2


def test_personal_catalog_expires_and_stale_catalog_is_used_on_failure():
    clock = [1000.0]
    static_df = _catalog(1.0)
    personal_df = _catalog(0.5)
    refreshed_df = _catalog(0.25)
    fetch = mock.Mock(side_effect=[
        personal_df,
        RuntimeError('Nebius is unavailable'),
        refreshed_df,
    ])

    with mock.patch.object(nebius_catalog, '_static_df', static_df), \
         mock.patch.object(nebius_catalog.skypilot_config,
                           'get_nested',
                           return_value=True), \
         mock.patch('sky.adaptors.nebius.get_tenant_id',
                    return_value='tenant-a'), \
         mock.patch.object(nebius_catalog.time,
                           'time',
                           side_effect=lambda: clock[0]), \
         mock.patch.object(nebius_catalog.os.path,
                           'getmtime',
                           side_effect=lambda _: clock[0]), \
         mock.patch.object(nebius_catalog, '_fetch_user_catalog', fetch), \
         mock.patch.object(nebius_catalog, 'logger'):
        result = nebius_catalog._get_df()
        assert result.iloc[0]['Price'] == 0.5
        assert fetch.call_count == 1

        clock[0] += nebius_catalog._PULL_FREQUENCY_HOURS * 3600 + 1
        result = nebius_catalog._get_df()
        assert result.iloc[0]['Price'] == 0.5
        assert fetch.call_count == 2

        # The failed refresh is cached and serves stale personalized pricing.
        assert nebius_catalog._get_df() is result
        assert fetch.call_count == 2

        clock[0] += nebius_catalog._PERSONAL_PRICING_RETRY_SECONDS + 1
        result = nebius_catalog._get_df()
        assert result.iloc[0]['Price'] == 0.25
        assert fetch.call_count == 3


def test_slow_tenant_fetch_does_not_block_other_tenant_cache_hit():
    static_df = _catalog(1.0)
    tenant_b_df = _catalog(0.4)
    nebius_catalog._personal_catalogs[
        'tenant-b'] = nebius_catalog._PersonalCatalogEntry(
            df=tenant_b_df, expires_at=float('inf'))

    fetch_started = threading.Event()
    allow_fetch = threading.Event()
    tenant_b_done = threading.Event()
    results = {}
    errors = []

    def get_tenant_id():
        return threading.current_thread().name

    def fetch_catalog():
        fetch_started.set()
        assert allow_fetch.wait(timeout=5)
        return _catalog(0.5)

    def get_catalog(key: str):
        try:
            results[key] = nebius_catalog._get_df()
        except Exception as e:  # pylint: disable=broad-except
            errors.append(e)
        finally:
            if key == 'tenant-b':
                tenant_b_done.set()

    with mock.patch.object(nebius_catalog, '_static_df', static_df), \
         mock.patch.object(nebius_catalog.skypilot_config,
                           'get_nested',
                           return_value=True), \
         mock.patch('sky.adaptors.nebius.get_tenant_id',
                    side_effect=get_tenant_id), \
         mock.patch.object(nebius_catalog, '_fetch_user_catalog',
                           side_effect=fetch_catalog), \
         mock.patch.object(nebius_catalog.os.path,
                           'getmtime',
                           return_value=1000.0):
        tenant_a_thread = threading.Thread(target=get_catalog,
                                           args=('tenant-a',),
                                           name='tenant-a')
        tenant_b_thread = threading.Thread(target=get_catalog,
                                           args=('tenant-b',),
                                           name='tenant-b')
        tenant_a_thread.start()
        try:
            assert fetch_started.wait(timeout=2)
            tenant_b_thread.start()
            assert tenant_b_done.wait(timeout=2)
        finally:
            allow_fetch.set()
            tenant_a_thread.join(timeout=5)
            if tenant_b_thread.ident is not None:
                tenant_b_thread.join(timeout=5)

    assert not errors
    assert results['tenant-b'] is tenant_b_df
    assert results['tenant-a'].iloc[0]['Price'] == 0.5
