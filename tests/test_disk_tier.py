import sky, pytest
from sky.utils import resources_utils
from click import testing as cli_testing
from sky import clouds
import sky.cli as cli
from sky import exceptions

def test_disk_tier_mismatch(enable_all_clouds):
    for cloud in clouds.CLOUD_REGISTRY.values():
        for tier in cloud._SUPPORTED_DISK_TIERS:
            sky.Resources(cloud=cloud, disk_tier=tier)
        for unsupported_tier in (set(resources_utils.DiskTier) - cloud._SUPPORTED_DISK_TIERS):
            with pytest.raises(exceptions.NotSupportedError) as e:
                sky.Resources(cloud=cloud, disk_tier=unsupported_tier)
            assert f'is not supported' in str(e.value), str(e.value)
