"""Tests for the AWS availability zone mapping cache."""
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import aws_catalog
from sky.catalog import common as catalog_common
from sky.catalog.data_fetchers import fetch_aws

_OLD_MAPPING = pd.DataFrame({
    'AvailabilityZoneName': ['ap-south-1a'],
    'AvailabilityZone': ['aps1-az1'],
})
_NEW_MAPPING = pd.DataFrame({
    'AvailabilityZoneName': ['ap-south-1a', 'ap-south-2a'],
    'AvailabilityZone': ['aps1-az1', 'aps2-az1'],
})


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_common, '_ABSOLUTE_VERSIONED_CATALOG_DIR',
                        str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_fetch():
    with mock.patch.object(fetch_aws,
                           'fetch_availability_zone_mappings',
                           return_value=_NEW_MAPPING) as m:
        yield m


def _write_mapping(catalog_dir, regions_marker=None):
    (catalog_dir / 'aws').mkdir(parents=True, exist_ok=True)
    _OLD_MAPPING.to_csv(catalog_dir / 'aws' / 'az_mappings-abc.csv',
                        index=False)
    if regions_marker is not None:
        meta_dir = catalog_dir / '.meta' / 'aws'
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / 'az_mappings-abc.csv.regions').write_text(regions_marker)


def test_first_fetch_records_regions(catalog_dir, mock_fetch):
    df = aws_catalog._get_az_mappings('abc')
    pd.testing.assert_frame_equal(df, _NEW_MAPPING)
    marker = catalog_dir / '.meta' / 'aws' / 'az_mappings-abc.csv.regions'
    assert marker.read_text() == aws_catalog._supported_regions_str()
    assert (catalog_dir / '.meta' / 'aws' / 'az_mappings-abc.csv.md5').exists()
    # The second call is served from the cache.
    aws_catalog._get_az_mappings('abc')
    assert mock_fetch.call_count == 1


def test_cached_mapping_with_current_regions_is_reused(catalog_dir, mock_fetch):
    _write_mapping(catalog_dir, aws_catalog._supported_regions_str())
    df = aws_catalog._get_az_mappings('abc')
    pd.testing.assert_frame_equal(df, _OLD_MAPPING)
    mock_fetch.assert_not_called()


@pytest.mark.parametrize(
    'regions_marker',
    [
        # Written before the region marker existed.
        None,
        # Written before a region was added to fetch_aws.ALL_REGIONS.
        'ap-south-1',
    ])
def test_stale_mapping_is_refetched(catalog_dir, mock_fetch, regions_marker):
    _write_mapping(catalog_dir, regions_marker)
    df = aws_catalog._get_az_mappings('abc')
    pd.testing.assert_frame_equal(df, _NEW_MAPPING)
    mock_fetch.assert_called_once()
    on_disk = pd.read_csv(catalog_dir / 'aws' / 'az_mappings-abc.csv')
    pd.testing.assert_frame_equal(on_disk, _NEW_MAPPING)
    # The md5 must follow the new content, or the refreshed mapping would be
    # treated as manually modified and uploaded to controllers.
    assert not catalog_common.is_catalog_modified('aws/az_mappings-abc.csv')


def test_stale_mapping_is_kept_when_refetch_fails(catalog_dir, mock_fetch):
    _write_mapping(catalog_dir)
    mock_fetch.side_effect = RuntimeError('no credentials')
    df = aws_catalog._get_az_mappings('abc')
    pd.testing.assert_frame_equal(df, _OLD_MAPPING)


def test_fetch_failure_without_cache_raises(catalog_dir, mock_fetch):
    mock_fetch.side_effect = RuntimeError('no credentials')
    with pytest.raises(RuntimeError):
        aws_catalog._get_az_mappings('abc')


def test_stale_mapping_is_used_when_identity_is_unknown(catalog_dir,
                                                        mock_fetch):
    _write_mapping(catalog_dir)
    df = aws_catalog._get_az_mappings('abc', can_fetch=False)
    pd.testing.assert_frame_equal(df, _OLD_MAPPING)
    mock_fetch.assert_not_called()


def test_default_identity_without_cache(catalog_dir, mock_fetch):
    assert aws_catalog._get_az_mappings('default', can_fetch=False) is None
    mock_fetch.assert_not_called()
