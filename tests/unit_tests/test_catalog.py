import os
import tempfile
import time
from unittest import mock

import numpy as np
import orjson
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
        annotations.clear_request_level_cache()

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


def test_get_hourly_cost_returns_python_float():
    """Test that get_hourly_cost_impl returns Python float, not numpy.float64.

    This is a regression test for GitHub issue #7969 where numpy.float64
    values couldn't be serialized by orjson in the API server.
    """
    df = pd.DataFrame([
        {
            'InstanceType': 'test-instance',
            'Price': np.float64(1.5),
            'SpotPrice': np.float64(0.5),
            'Region': 'us-west1',
            'AvailabilityZone': 'us-west1-a'
        },
    ])

    # Test on-demand pricing
    cost = catalog_common.get_hourly_cost_impl(df,
                                               'test-instance',
                                               use_spot=False,
                                               region=None,
                                               zone=None)
    assert isinstance(cost, float)
    assert not isinstance(cost, np.floating)
    assert type(cost) == float

    # Test spot pricing
    spot_cost = catalog_common.get_hourly_cost_impl(df,
                                                    'test-instance',
                                                    use_spot=True,
                                                    region=None,
                                                    zone=None)
    assert isinstance(spot_cost, float)
    assert not isinstance(spot_cost, np.floating)
    assert type(spot_cost) == float


def test_catalog_prices_are_json_serializable():
    """Test that catalog prices can be serialized with orjson.

    This is a regression test for GitHub issue #7969 where the API server
    failed to serialize cost_report responses containing numpy.float64 values.
    """
    df = pd.DataFrame([
        {
            'InstanceType': 'test-instance',
            'Price': np.float64(2.5),
            'SpotPrice': np.float64(1.0),
            'Region': 'us-west1',
        },
    ])

    cost = catalog_common.get_hourly_cost_impl(df,
                                               'test-instance',
                                               use_spot=False,
                                               region=None,
                                               zone=None)

    # Should serialize without TypeError
    serialized = orjson.dumps(cost)
    assert serialized == b'2.5'

    # Should also work in a dict (simulating API response)
    response = {'total_cost': cost}
    serialized_dict = orjson.dumps(response)
    assert orjson.loads(serialized_dict) == {'total_cost': 2.5}


# Synthetic catalog DataFrame for local disk tests.
# Mirrors real AWS patterns:
#   m5.large    - no local disk         ($0.10)
#   d2.large    - SSD, no NVMe, 500 GB  ($0.14)
#   i3.large    - SSD + NVMe, 475 GB    ($0.16)
#   i3.2xlarge  - SSD + NVMe, 1900 GB   ($0.62)
_LOCAL_DISK_DF = pd.DataFrame([
    {
        'InstanceType': 'm5.large',
        'vCPUs': 2,
        'MemoryGiB': 8,
        'Price': 0.10,
        'Region': 'us-east-1',
        'AvailabilityZone': 'us-east-1a',
        'LocalDiskType': float('nan'),
        'LocalDiskSize': float('nan'),
        'LocalDiskCount': float('nan'),
        'NVMeSupported': False,
    },
    {
        'InstanceType': 'i3.large',
        'vCPUs': 2,
        'MemoryGiB': 15.25,
        'Price': 0.16,
        'Region': 'us-east-1',
        'AvailabilityZone': 'us-east-1a',
        'LocalDiskType': 'ssd',
        'LocalDiskSize': 475.0,
        'LocalDiskCount': 1,
        'NVMeSupported': True,
    },
    {
        'InstanceType': 'i3.2xlarge',
        'vCPUs': 8,
        'MemoryGiB': 61,
        'Price': 0.62,
        'Region': 'us-east-1',
        'AvailabilityZone': 'us-east-1a',
        'LocalDiskType': 'ssd',
        'LocalDiskSize': 950.0,
        'LocalDiskCount': 2,
        'NVMeSupported': True,
    },
    {
        'InstanceType': 'd2.large',
        'vCPUs': 2,
        'MemoryGiB': 15.25,
        'Price': 0.14,
        'Region': 'us-east-1',
        'AvailabilityZone': 'us-east-1a',
        'LocalDiskType': 'ssd',
        'LocalDiskSize': 500.0,
        'LocalDiskCount': 1,
        'NVMeSupported': False,
    },
])


@pytest.mark.parametrize(
    'local_disk, expected',
    [
        # NVMe at-least: cheapest NVMe with total >= 500 is i3.2xlarge
        # (i3.large only has 475)
        ('nvme:500+', 'i3.2xlarge'),
        # NVMe at-least: only i3.2xlarge has total >= 1500
        ('nvme:1500+', 'i3.2xlarge'),
        # NVMe at-least: i3.large (475 >= 100) is cheapest NVMe
        ('nvme:100+', 'i3.large'),
        # SSD at-least: NVMe instances also qualify; d2.large ($0.14,
        # 500 GB) is cheapest SSD with >= 400
        ('ssd:400+', 'd2.large'),
        # NVMe exact: only i3.large has total ~475
        ('nvme:475', 'i3.large'),
        # NVMe exact: no instance has exactly 300 GB NVMe
        ('nvme:300', None),
        # SSD exact: d2.large has 500 GB total (non-NVMe SSD)
        ('ssd:500', 'd2.large'),
        # No local disk: cheapest instance overall (m5.large)
        (None, 'm5.large'),
        # Size too large: nothing has >= 5000
        ('nvme:5000+', None),
    ],
)
def test_filter_with_local_disk(local_disk, expected):
    """Test that filter_with_local_disk + instance selection picks the
    correct (cheapest) instance satisfying local disk requirements."""
    filtered = catalog_common.filter_with_local_disk(_LOCAL_DISK_DF.copy(),
                                                     local_disk)
    result = catalog_common.get_instance_type_for_cpus_mem_impl(
        filtered, cpus='1+', memory_gb_or_ratio=None, region=None)
    assert result == expected


@pytest.mark.parametrize(
    'local_disk, expected_instances',
    [
        # NVMe filters out non-NVMe and non-SSD instances
        ('nvme:100+', ['i3.2xlarge', 'i3.large']),
        # SSD includes NVMe instances too (NVMe is a superset of SSD)
        ('ssd:100+', ['d2.large', 'i3.2xlarge', 'i3.large']),
        # None returns all instances (no filtering)
        (None, ['d2.large', 'i3.2xlarge', 'i3.large', 'm5.large']),
    ],
)
def test_filter_with_local_disk_instance_sets(local_disk, expected_instances):
    """Test that filter_with_local_disk returns the correct candidate set."""
    filtered = catalog_common.filter_with_local_disk(_LOCAL_DISK_DF.copy(),
                                                     local_disk)
    assert sorted(
        filtered['InstanceType'].tolist()) == sorted(expected_instances)
