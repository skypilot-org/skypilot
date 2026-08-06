# pylint: disable=protected-access
import logging
import os
from unittest.mock import MagicMock
from unittest.mock import patch

from sky import clouds
from sky import resources as resources_lib
from sky.adaptors import nebius as nebius_adaptor
from sky.clouds import nebius
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

    def test_grpc_aio_poller_filter_drops_only_poller_noise(self):
        log_filter = nebius_adaptor._GrpcAioPollerNoiseFilter()

        def _record(msg: str) -> logging.LogRecord:
            return logging.LogRecord(name='asyncio',
                                     level=logging.ERROR,
                                     pathname='asyncio/base_events.py',
                                     lineno=1,
                                     msg=msg,
                                     args=None,
                                     exc_info=None)

        # As emitted by asyncio's default exception handler when grpc.aio
        # polling raises (e.g. BlockingIOError) in a loop callback.
        poller_msg = (
            'Exception in callback PollerCompletionQueue._handle_events('
            '<_UnixSelecto...e debug=False>)()\n'
            'handle: <Handle PollerCompletionQueue._handle_events('
            '<_UnixSelecto...e debug=False>)()>')
        assert not log_filter.filter(_record(poller_msg))
        # Unrelated asyncio error reporting must be unaffected.
        assert log_filter.filter(_record('Task exception was never retrieved'))
        assert log_filter.filter(_record('Exception in callback my_callback()'))

    def test_set_nebius_loggers_suppresses_poller_noise_end_to_end(self):
        records = []

        class _CaptureHandler(logging.Handler):

            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        asyncio_logger = logging.getLogger('asyncio')
        deprecation_logger = logging.getLogger('deprecation')
        previous_filters = list(asyncio_logger.filters)
        previous_deprecation_filters = list(deprecation_logger.filters)
        previous_grpc_verbosity = os.environ.get('GRPC_VERBOSITY')
        handler = _CaptureHandler(level=logging.DEBUG)
        asyncio_logger.addHandler(handler)
        try:
            nebius_adaptor._set_nebius_loggers()
            asyncio_logger.error('Exception in callback '
                                 'PollerCompletionQueue._handle_events('
                                 '<_UnixSelecto...e debug=False>)()')
            asyncio_logger.error('some real asyncio error')
        finally:
            asyncio_logger.removeHandler(handler)
            asyncio_logger.filters[:] = previous_filters
            deprecation_logger.filters[:] = previous_deprecation_filters
            if previous_grpc_verbosity is None:
                os.environ.pop('GRPC_VERBOSITY', None)
            else:
                os.environ['GRPC_VERBOSITY'] = previous_grpc_verbosity

        messages = [r.getMessage() for r in records]
        assert not any(
            'PollerCompletionQueue' in m for m in messages), (messages)
        assert any('some real asyncio error' in m for m in messages), messages
