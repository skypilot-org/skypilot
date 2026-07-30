"""Tests for SkyPilot package dependency extras."""

from sky.setup_files import dependencies


def test_all_except_azure_extra_installs_every_available_cloud_but_azure():
    expected_clouds = set(dependencies.clouds_for_all)
    expected_clouds.add('vast')
    expected_clouds.remove('azure')
    expected_dependencies = set().union(
        *(dependencies.cloud_extras[cloud] for cloud in expected_clouds))

    assert set(dependencies.extras_require['all-except-azure']
              ) == expected_dependencies
