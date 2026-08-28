"""Tests for the AWS service-catalog fetcher."""

from sky.catalog.data_fetchers import fetch_aws


def test_sao_paulo_region_is_fetched() -> None:
    assert 'sa-east-1' in fetch_aws.ALL_REGIONS


def test_zurich_region_is_fetched() -> None:
    assert 'eu-central-2' in fetch_aws.ALL_REGIONS
