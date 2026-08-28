"""Tests for the AWS service-catalog fetcher."""

from pathlib import Path
import runpy
import sys

from sky.catalog.data_fetchers import fetch_aws


def test_sao_paulo_region_is_fetched() -> None:
    assert 'sa-east-1' in fetch_aws.ALL_REGIONS


def test_zurich_region_is_fetched() -> None:
    assert 'eu-central-2' in fetch_aws.ALL_REGIONS


def test_malaysia_region_is_fetched() -> None:
    assert 'ap-southeast-5' in fetch_aws.ALL_REGIONS


def test_malaysia_region_is_a_curated_image_copy_target(monkeypatch) -> None:
    image_gen_path = (Path(__file__).parents[2] / 'sky' / 'catalog' / 'images' /
                      'aws_utils' / 'image_gen.py')
    monkeypatch.setattr(
        sys, 'argv',
        [str(image_gen_path), '--image-id', 'ami-test', '--processor', 'gpu'])

    image_gen_globals = runpy.run_path(str(image_gen_path))

    assert 'ap-southeast-5' in image_gen_globals['ALL_REGIONS']
