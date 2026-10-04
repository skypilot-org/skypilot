# pylint: disable=protected-access
import logging
import os
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.adaptors import nebius as nebius_adaptor
from sky.clouds import nebius
from sky.provision.nebius import constants as nebius_constants
from sky.utils import resources_utils


class TestNebiusAdaptorSDK:
    """Tests for SkyPilot's Nebius SDK configuration."""

    def test_user_agent_prefix_is_passed_for_all_credentials(self):
        mock_nebius = MagicMock()
        with patch.object(nebius_adaptor, 'nebius', mock_nebius), \
             patch.object(nebius_adaptor,
                          'api_domain',
                          return_value='api.nebius.test'), \
             patch('sky.__version__', '1.2.3'):
            nebius_adaptor._sdk.cache_clear()
            try:
                nebius_adaptor._sdk('iam-token', None)
                nebius_adaptor._sdk(None, '~/credentials.json')
            finally:
                nebius_adaptor._sdk.cache_clear()

        calls = mock_nebius.sdk.SDK.call_args_list
        assert calls[0].kwargs == {
            'credentials': 'iam-token',
            'domain': 'api.nebius.test',
            'user_agent_prefix': 'skypilot/1.2.3',
        }
        assert calls[1].kwargs == {
            'credentials_file_name': os.path.expanduser('~/credentials.json'),
            'domain': 'api.nebius.test',
            'user_agent_prefix': 'skypilot/1.2.3',
        }


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

    def test_network_tier_supported_for_blackwell_8gpu(self):
        """Test that network_tier=best is supported for B200:8 and B300:8.

        The Blackwell platforms are InfiniBand-capable but were rejected
        because the accelerator check only listed H100/H200.
        """
        for acc in ['B200', 'B300']:
            resources = resources_lib.Resources(cloud=nebius.Nebius(),
                                                accelerators={acc: 8},
                                                network_tier='best')

            unsupported_features = (
                nebius.Nebius._unsupported_features_for_resources(resources))

            assert (clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER
                    not in unsupported_features), acc

    def test_network_tier_unsupported_for_blackwell_partial_node(self):
        """Only 8-GPU instances can join an InfiniBand GPU cluster."""
        resources = resources_lib.Resources(cloud=nebius.Nebius(),
                                            accelerators={'B300': 1},
                                            network_tier='best')

        unsupported_features = nebius.Nebius._unsupported_features_for_resources(
            resources)

        assert (clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER
                in unsupported_features)

    @patch('sky.provision.nebius.utils.get_project_by_region',
           return_value='test-project-id')
    @patch('sky.skypilot_config.get_nested')
    def test_no_infiniband_options_without_docker(self, mock_get_nested,
                                                  mock_get_project):
        """Test that InfiniBand options are not added without Docker image."""
        del mock_get_project  # unused: stops make_deploy_resources_variables
        # from hitting the Nebius IAM API in unit-test environments.
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
            cluster_name=resources_utils.ClusterName(
                display_name='test-cluster', name_on_cloud='test-cluster'),
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

    @patch('sky.provision.nebius.utils.get_project_by_region',
           return_value='test-project-id')
    @patch('sky.skypilot_config.get_nested')
    def test_no_infiniband_options_without_network_tier_best(
            self, mock_get_nested, mock_get_project):
        """Test that InfiniBand options are not added without network_tier=best."""
        del mock_get_project  # unused: stops make_deploy_resources_variables
        # from hitting the Nebius IAM API in unit-test environments.
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
            cluster_name=resources_utils.ClusterName(
                display_name='test-cluster', name_on_cloud='test-cluster'),
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


class TestNebiusAdaptorLogging:
    """Nebius SDK log noise must not reach user-facing CLI output."""

    def test_loop_exception_handler_logs_at_debug(self):
        """Callback exceptions in the SDK loop are logged at debug only."""
        records = []

        class _CaptureHandler(logging.Handler):

            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _CaptureHandler(level=logging.DEBUG)
        previous_level = nebius_adaptor.logger.level
        nebius_adaptor.logger.addHandler(handler)
        nebius_adaptor.logger.setLevel(logging.DEBUG)
        try:
            context = {
                'message': ('Exception in callback '
                            'PollerCompletionQueue._handle_events'),
                'exception': RuntimeError('unresolved sku'),
            }
            nebius_adaptor._loop_exception_handler(MagicMock(), context)
        finally:
            nebius_adaptor.logger.removeHandler(handler)
            nebius_adaptor.logger.setLevel(previous_level)

        assert records, 'Expected a debug record for diagnostics'
        assert all(r.levelno == logging.DEBUG for r in records)

    def test_dedicated_loop_has_exception_handler(self):
        with patch.object(nebius_adaptor, '_loop', None):
            loop = nebius_adaptor._get_event_loop()
            try:
                assert (loop.get_exception_handler() is
                        nebius_adaptor._loop_exception_handler)
            finally:
                loop.call_soon_threadsafe(loop.stop)

    def test_deprecation_filter_drops_only_nebius_records(self):
        log_filter = nebius_adaptor._NebiusDeprecationFilter()

        def _record(pathname: str) -> logging.LogRecord:
            return logging.LogRecord(name='deprecation',
                                     level=logging.WARNING,
                                     pathname=pathname,
                                     lineno=1,
                                     msg='Field x is deprecated',
                                     args=None,
                                     exc_info=None)

        nebius_path = os.path.join('site-packages', 'nebius', 'aio',
                                   'client.py')
        other_path = os.path.join('site-packages', 'otherlib', 'client.py')
        assert not log_filter.filter(_record(nebius_path))
        assert log_filter.filter(_record(other_path))


class TestNebiusInfinibandFabrics:
    """Tests for InfiniBand fabric selection per platform and region."""

    def test_default_fabric_for_known_platforms(self):
        """Every fabric is looked up by both platform and region."""
        expected = {
            ('gpu-h100-sxm', 'eu-north1'): 'fabric-2',
            ('gpu-h200-sxm', 'eu-west1'): 'fabric-5',
            ('gpu-h200-sxm', 'us-central1'): 'us-central1-a',
            ('gpu-b200-sxm', 'us-central1'): 'us-central1-b',
            ('gpu-b200-sxm-a', 'me-west1'): 'me-west1-a',
            ('gpu-b300-sxm', 'uk-south1'): 'uk-south1-a',
        }
        for (platform, region), fabric in expected.items():
            assert nebius_constants.get_default_fabric(platform,
                                                       region) == fabric

    def test_default_fabric_rejects_unavailable_combination(self):
        """An unknown platform/region must not fall back to another region.

        A fabric belongs to one platform in one region, so returning a default
        from elsewhere (previously eu-north1's H100 fabric) would attach the
        GPU cluster to a fabric that does not exist for the request.
        """
        with pytest.raises(ValueError, match='No InfiniBand fabric'):
            nebius_constants.get_default_fabric('gpu-b300-sxm', 'eu-north1')
        with pytest.raises(ValueError, match='No InfiniBand fabric'):
            nebius_constants.get_default_fabric('gpu-l40s', 'uk-south1')

    def test_infiniband_platforms_cover_mapped_fabrics(self):
        """Every platform with a fabric must be treated as InfiniBand-capable."""
        for platform, _ in nebius_constants.INFINIBAND_FABRIC_MAPPING:
            assert platform in nebius_constants.INFINIBAND_INSTANCE_PLATFORMS

    def test_preset_prefix_matches_8gpu_presets(self):
        """The GPU-cluster preset check must cover every 8-GPU shape.

        The vCPU/memory portion differs per platform, so an exact-match check
        silently skipped GPU cluster creation on B200 and B300.
        """
        prefix = nebius_constants.INFINIBAND_PRESET_PREFIX
        for preset in [
                '8gpu-128vcpu-1600gb',  # H100, H200
                '8gpu-160vcpu-1792gb',  # B200
                '8gpu-192vcpu-2768gb',  # B300
        ]:
            assert preset.startswith(prefix)
        assert not '1gpu-16vcpu-200gb'.startswith(prefix)
