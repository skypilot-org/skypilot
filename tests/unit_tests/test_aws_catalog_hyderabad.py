"""Tests for Hyderabad in the AWS service-catalog source."""

from sky.catalog.data_fetchers import fetch_aws


def test_hyderabad_region_is_fetched() -> None:
    assert 'ap-south-2' in fetch_aws.ALL_REGIONS
