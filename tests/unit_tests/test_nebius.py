from unittest.mock import MagicMock
from unittest.mock import patch

from sky import clouds
from sky import resources as resources_lib
from sky.clouds import nebius


class TestNebiusNetworkTier:
    """Test cases for Nebius network_tier functionality."""

    def test_network_tier_unsupported_for_non_infiniband_gpus(self):
        """Test that network_tier=best is unsupported for non-InfiniBand GPUs."""
        # Test with L40S (should not support network_tier=best)
        resources = resources_lib.Resources(cloud=nebius.Nebius(),
                                            accelerators={'L40S': 4},
                                            network_tier='best')

        unsupported_features = nebius.Nebius._unsupported_features_for_resources(
            resources)

        # Should still have CUSTOM_NETWORK_TIER as unsupported
        assert clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER in unsupported_features

    def test_network_tier_supported_for_h100_8gpu(self):
        """Test that network_tier=best is supported for H100:8."""
        resources = resources_lib.Resources(cloud=nebius.Nebius(),
                                            accelerators={'H100': 8},
                                            network_tier='best')

        unsupported_features = nebius.Nebius._unsupported_features_for_resources(
            resources)

        # Should NOT have CUSTOM_NETWORK_TIER as unsupported
        assert clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER not in unsupported_features

    def test_network_tier_supported_for_h200_8gpu(self):
        """Test that network_tier=best is supported for H200:8."""
        resources = resources_lib.Resources(cloud=nebius.Nebius(),
                                            accelerators={'H200': 8},
                                            network_tier='best')

        unsupported_features = nebius.Nebius._unsupported_features_for_resources(
            resources)

        # Should NOT have CUSTOM_NETWORK_TIER as unsupported
        assert clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER not in unsupported_features

    @patch('sky.skypilot_config.get_nested')
    def test_no_infiniband_options_without_docker(self, mock_get_nested):
        """Test that InfiniBand options are not added without Docker image."""
        mock_get_nested.return_value = []  # No filesystems

        # Create resources with H200:8, network_tier=best, but NO Docker image
        resources = resources_lib.Resources(
            cloud=nebius.Nebius(),
            accelerators={'H200': 8},
            network_tier='best',
            instance_type='gpu-h200-sxm_8gpu-128vcpu-1600gb')
        resources = resources.assert_launchable()

        cloud = nebius.Nebius()
        region = MagicMock()
        region.name = 'us-central1'

        deploy_vars = cloud.make_deploy_resources_variables(
            resources=resources,
            cluster_name='test-cluster',
            region=region,
            zones=None,
            num_nodes=1)

        # Check that Docker run options only include GPU access (if any)
        docker_options = deploy_vars.get('docker_run_options', [])

        # Should include GPU access
        assert '--gpus all' in docker_options

        # Should NOT include InfiniBand options since no Docker image
        assert '--device=/dev/infiniband' not in docker_options
        assert '--cap-add=IPC_LOCK' not in docker_options

    @patch('sky.skypilot_config.get_nested')
    def test_no_infiniband_options_without_network_tier_best(
            self, mock_get_nested):
        """Test that InfiniBand options are not added without network_tier=best."""
        mock_get_nested.return_value = []  # No filesystems

        # Create resources with H200:8, Docker image, but NO network_tier=best
        resources = resources_lib.Resources(
            cloud=nebius.Nebius(),
            accelerators={'H200': 8},
            image_id='docker:test-image:latest',
            instance_type='gpu-h200-sxm_8gpu-128vcpu-1600gb')
        resources = resources.assert_launchable()

        cloud = nebius.Nebius()
        region = MagicMock()
        region.name = 'us-central1'

        deploy_vars = cloud.make_deploy_resources_variables(
            resources=resources,
            cluster_name='test-cluster',
            region=region,
            zones=None,
            num_nodes=1)

        # Check that Docker run options only include GPU access
        docker_options = deploy_vars['docker_run_options']

        # Should include GPU access
        assert '--gpus all' in docker_options

        # Should NOT include InfiniBand options since network_tier != best
        assert '--device=/dev/infiniband' not in docker_options
        assert '--cap-add=IPC_LOCK' not in docker_options
