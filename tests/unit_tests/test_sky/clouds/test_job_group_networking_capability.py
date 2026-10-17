"""Tests for the per-cloud in-group (Job Group) networking capability."""
import pytest

from sky import clouds


@pytest.mark.parametrize('cloud_cls,expected', [
    (clouds.Kubernetes, True),
    (clouds.AWS, True),
    (clouds.GCP, True),
    (clouds.Azure, False),
    (clouds.Lambda, False),
    (clouds.RunPod, False),
])
def test_supports_job_group_networking(cloud_cls, expected):
    assert cloud_cls.supports_job_group_networking() is expected


def test_base_cloud_default_is_false():
    assert clouds.Cloud.supports_job_group_networking() is False
