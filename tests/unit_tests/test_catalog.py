import os
import tempfile
import time
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import common as catalog_common
from sky.utils import annotations


@mock.patch('sky.catalog.common.requests.get')
def test_read_catalog_triggers_update_on_stale_file(mock_get):
    """Test that read_catalog (and the LazyDataFrame it returns)
    does an update when the catalog file is stale, and that
    it's cached for the duration of the request."""
    DUMMY_CSV = 'col1,col2\n1,2\n3,4\n'
    NEW_DUMMY_CSV = 'col1,col2\n5,6\n7,8\n'

    class DummyResponse:

        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code != 200:
                raise Exception('HTTP error')

    mock_get.return_value = DummyResponse(DUMMY_CSV)

    # Create a random file name.
    with tempfile.NamedTemporaryFile(delete=True) as tmp:
        filename = os.path.join(f'gcp/{os.path.basename(tmp.name)}.csv')

    try:
        df = catalog_common.read_catalog(filename, pull_frequency_hours=1)
        # Force the CSV to be written to disk.
        df.head()

        # The file on disk and the DataFrame should match DUMMY_CSV.
        abs_catalog_path = catalog_common.get_catalog_path(filename)
        assert os.path.exists(abs_catalog_path)
        with open(abs_catalog_path) as f:
            content_on_disk = f.read()
        assert content_on_disk == DUMMY_CSV
        pd.testing.assert_frame_equal(df._df, pd.read_csv(abs_catalog_path))

        # Modify the file's mtime to be 2 hours ago.
        new_time = time.time() - 60 * 60 * 2
        os.utime(abs_catalog_path, (new_time, new_time))

        # Patch requests.get again to return NEW_DUMMY_CSV.
        mock_get.return_value = DummyResponse(NEW_DUMMY_CSV)

        # We haven't cleared annotations.FUNCTIONS_NEED_RELOAD_CACHE,
        # so _load_df should still be cached and update_if_stale_func
        # should not be called, i.e. the file on disk and
        # DataFrame should not be updated.
        df.head()
        pd.testing.assert_frame_equal(df._df, pd.read_csv(abs_catalog_path))

        # Clear the cache.
        for func in annotations.FUNCTIONS_NEED_RELOAD_CACHE:
            func.cache_clear()

        # Now update_if_stale_func should be called and should trigger a new fetch.
        df.head()
        # The file and DataFrame should match NEW_DUMMY_CSV.
        with open(abs_catalog_path) as f:
            content_on_disk = f.read()
        assert content_on_disk == NEW_DUMMY_CSV
        pd.testing.assert_frame_equal(df._df, pd.read_csv(abs_catalog_path))
    finally:
        if os.path.exists(abs_catalog_path):
            os.remove(abs_catalog_path)
        meta_path = os.path.join(catalog_common._ABSOLUTE_VERSIONED_CATALOG_DIR,
                                 '.meta', filename + '.md5')
        if os.path.exists(meta_path):
            os.remove(meta_path)
        lock_path = os.path.join(catalog_common._ABSOLUTE_VERSIONED_CATALOG_DIR,
                                 '.meta', filename + '.lock')
        if os.path.exists(lock_path):
            os.remove(lock_path)


@pytest.mark.parametrize(
    "cpus, memory, region, zone, expected",
    [
        ('4', '16', None, None, 'a'),  # Exact match
        ('4+', '16+', None, None, 'a'),  # At least match, cheapest
        ('16', '128', None, None, None),  # No match
        ('1+', None, 'asia-southeast1', None, 'd'),  # Region filtering
        ('1+', None, 'us-west1', 'us-west1-b', 'b'),  # Zone filtering
        ('1+', None, 'us-west1', 'us-west1-c',
         None),  # Zone filtering, no match
        # Regression test for https://github.com/skypilot-org/skypilot/pull/6293:
        # b is cheaper but only available in us-west1-b; so c is chosen.
        ('8', '32', 'us-west1', 'us-west1-a', 'c'),
    ])
def test_get_instance_type_for_cpus_mem_impl_with_az(cpus, memory, region, zone,
                                                     expected):
    """Test get_instance_type_for_cpus_mem_impl with a DataFrame that includes AvailabilityZone."""
    df = pd.DataFrame([
        {
            'InstanceType': 'a',
            'vCPUs': 4,
            'MemoryGiB': 16,
            'Price': 1.0,
            'Region': 'us-west1',
            'AvailabilityZone': 'us-west1-a'
        },
        {
            'InstanceType': 'b',
            'vCPUs': 8,
            'MemoryGiB': 32,
            'Price': 2.0,
            'Region': 'us-west1',
            'AvailabilityZone': 'us-west1-b'
        },
        {
            'InstanceType': 'c',
            'vCPUs': 8,
            'MemoryGiB': 32,
            'Price': 5.0,
            'Region': 'us-west1',
            'AvailabilityZone': 'us-west1-a'
        },
        {
            'InstanceType': 'd',
            'vCPUs': 8,
            'MemoryGiB': 32,
            'Price': 3.0,
            'Region': 'asia-southeast1',
            'AvailabilityZone': 'asia-southeast1-a'
        },
    ])
    result = catalog_common.get_instance_type_for_cpus_mem_impl(
        df, cpus=cpus, memory_gb_or_ratio=memory, region=region, zone=zone)
    assert result == expected


@pytest.mark.parametrize(
    "cpus, memory, region, expected",
    [
        ('4', '16', None, 'a'),  # Exact match
        ('1+', None, None, 'a'),  # At least match, cheapest
        ('8+', '32+', None, 'c'),  # At least match, higher req
        ('16', '128', None, None),  # No match
        ('1+', None, 'asia-southeast1', 'b'),  # Region filtering, cheapest
        ('1+', None, 'europe-west1', None),  # Region filtering, no match
    ])
def test_get_instance_type_for_cpus_mem_impl_no_az(cpus, memory, region,
                                                   expected):
    """Test get_instance_type_for_cpus_mem_impl with a DataFrame that does not include AvailabilityZone."""
    df = pd.DataFrame([
        {
            'InstanceType': 'a',
            'vCPUs': 4,
            'MemoryGiB': 16,
            'Price': 1.0,
            'Region': 'us-east1'
        },
        {
            'InstanceType': 'b',
            'vCPUs': 4,
            'MemoryGiB': 16,
            'Price': 3.0,
            'Region': 'asia-southeast1'
        },
        {
            'InstanceType': 'c',
            'vCPUs': 8,
            'MemoryGiB': 32,
            'Price': 5.0,
            'Region': 'asia-southeast1'
        },
    ])
    result = catalog_common.get_instance_type_for_cpus_mem_impl(
        df, cpus=cpus, memory_gb_or_ratio=memory, region=region)
    assert result == expected
