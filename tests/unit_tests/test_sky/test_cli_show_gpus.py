"""Tests for show_gpus CLI command.

This module contains tests for the show_gpus function in
sky.client.cli.command module.
"""
from unittest import mock

from click.testing import CliRunner
import numpy as np
import pytest

from sky import clouds
from sky import models
from sky.catalog import common as catalog_common
from sky.client import sdk
from sky.client.cli import command
from sky.utils import registry


class TestShowGpus:
    """Test suite for the show_gpus function."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up common mocks for all tests."""
        self.runner = CliRunner()

        self.mock_cloud_registry = mock.patch.object(registry.CLOUD_REGISTRY,
                                                     'from_str')
        self.mock_sdk_get = mock.patch.object(sdk, 'get')
        self.mock_stream_and_get = mock.patch.object(sdk, 'stream_and_get')
        self.mock_enabled_clouds_fn = mock.patch.object(sdk, 'enabled_clouds')

        self.cloud_registry_mock = self.mock_cloud_registry.start()
        self.sdk_get_mock = self.mock_sdk_get.start()
        self.stream_and_get_mock = self.mock_stream_and_get.start()
        self.enabled_clouds_fn_mock = self.mock_enabled_clouds_fn.start()

        self.enabled_clouds_fn_mock.return_value = 'mock_request_id'
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {}

        yield

        self.mock_cloud_registry.stop()
        self.mock_sdk_get.stop()
        self.mock_stream_and_get.stop()
        self.mock_enabled_clouds_fn.stop()

    # ==========================================================================
    # Basic functionality tests
    # ==========================================================================

    def test_show_gpus_no_accelerator_no_cloud(self):
        """Test show_gpus with no accelerator string and no cloud filter."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {'V100': [1, 2, 4, 8]}

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, [])

        assert result.exit_code == 0
        assert 'V100' in result.output

    def test_show_gpus_with_specific_accelerator(self):
        """Test show_gpus with a specific accelerator string."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {}

        with mock.patch.object(sdk,
                               'list_accelerators',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, ['V100'])

        assert result.exit_code == 0
        assert 'not found' in result.output

    def test_show_gpus_with_accelerator_and_quantity(self):
        """Test show_gpus with accelerator string including quantity."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {}

        with mock.patch.object(sdk,
                               'list_accelerators',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, ['V100:4'])

        assert result.exit_code == 0
        assert 'not found' in result.output

    # ==========================================================================
    # Input validation tests
    # ==========================================================================

    def test_show_gpus_invalid_accelerator_format(self):
        """Test show_gpus with invalid accelerator format (too many colons)."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:4:extra'])

        assert result.exit_code != 0
        assert 'Invalid accelerator string' in result.output

    def test_show_gpus_invalid_quantity(self):
        """Test show_gpus with invalid quantity (non-integer)."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:abc'])

        assert result.exit_code != 0
        assert 'Invalid accelerator quantity' in result.output

    def test_show_gpus_negative_quantity(self):
        """Test show_gpus with negative quantity."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:-1'])

        assert result.exit_code != 0
        assert 'Invalid accelerator quantity' in result.output

    def test_show_gpus_zero_quantity(self):
        """Test show_gpus with zero quantity."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:0'])

        assert result.exit_code != 0
        assert 'Invalid accelerator quantity' in result.output

    def test_show_gpus_float_quantity(self):
        """Test show_gpus with float quantity."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:4.0'])

        assert result.exit_code != 0
        assert 'Invalid accelerator quantity' in result.output

    def test_show_gpus_empty_accelerator_string(self):
        """Test show_gpus with empty accelerator string after colon."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['V100:'])

        assert result.exit_code != 0
        assert 'Invalid accelerator quantity' in result.output

    # ==========================================================================
    # Flag validation tests
    # ==========================================================================

    def test_show_gpus_region_without_cloud(self):
        """Test that --region flag without --cloud raises error."""
        self.cloud_registry_mock.return_value = None

        result = self.runner.invoke(command.show_gpus,
                                    ['--region', 'us-west-2'])

        assert result.exit_code != 0
        assert 'The --region flag is only valid when the --cloud flag is set' in result.output

    def test_show_gpus_all_regions_without_accelerator(self):
        """Test that --all-regions flag without accelerator raises error."""
        self.cloud_registry_mock.return_value = None

        result = self.runner.invoke(command.show_gpus, ['--all-regions'])

        assert result.exit_code != 0
        assert 'The --all-regions flag is only valid when an accelerator is specified' in result.output

    def test_show_gpus_all_regions_with_region(self):
        """Test that --all-regions and --region cannot be used together."""
        mock_aws = clouds.AWS()
        self.cloud_registry_mock.return_value = mock_aws

        result = self.runner.invoke(command.show_gpus, [
            'V100', '--cloud', 'aws', '--region', 'us-west-2', '--all-regions'
        ])

        assert result.exit_code != 0
        assert '--all-regions and --region flags cannot be used simultaneously' in result.output

    def test_show_gpus_all_with_accelerator(self):
        """Test that --all flag cannot be used with accelerator name."""
        self.cloud_registry_mock.return_value = None

        result = self.runner.invoke(command.show_gpus, ['V100', '--all'])

        assert result.exit_code != 0
        assert '--all is only allowed without a GPU name' in result.output

    # ==========================================================================
    # Cloud filter tests
    # ==========================================================================

    def test_show_gpus_with_cloud_filter(self):
        """Test show_gpus with cloud filter."""
        mock_aws = clouds.AWS()
        self.cloud_registry_mock.return_value = mock_aws
        self.sdk_get_mock.return_value = ['AWS']
        self.stream_and_get_mock.return_value = {'V100': [1, 2, 4, 8]}

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, ['--cloud', 'aws'])

        assert result.exit_code == 0
        self.cloud_registry_mock.assert_called_once_with('aws')
        assert 'V100' in result.output

    def test_show_gpus_with_region_filter(self):
        """Test show_gpus with region filter."""
        mock_aws = clouds.AWS()
        self.cloud_registry_mock.return_value = mock_aws
        self.sdk_get_mock.return_value = ['AWS']
        self.stream_and_get_mock.return_value = {'V100': [1, 2, 4, 8]}

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(
                command.show_gpus, ['--cloud', 'aws', '--region', 'us-west-2'])

        assert result.exit_code == 0
        assert 'V100' in result.output

    def test_show_gpus_with_infra_option(self):
        """Test show_gpus with --infra option."""
        mock_aws = clouds.AWS()
        self.cloud_registry_mock.return_value = mock_aws
        self.sdk_get_mock.return_value = ['AWS']
        self.stream_and_get_mock.return_value = {'V100': [1, 2, 4, 8]}

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus,
                                        ['--infra', 'aws/us-west-2'])

        assert result.exit_code == 0
        assert 'V100' in result.output

    def test_show_gpus_wildcard_cloud_converted_to_none(self):
        """Test that wildcard cloud '*' is converted to None internally."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {}

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, ['--infra', 'aws'])

        assert result.exit_code == 0

    # ==========================================================================
    # --all flag tests
    # ==========================================================================

    def test_show_gpus_with_all_flag(self):
        """Test show_gpus with --all flag to show all accelerators."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {
            'V100': [1, 2, 4, 8],
            'A100': [1, 2, 4, 8],
            'T4': [1, 2, 4],
        }

        with mock.patch.object(sdk,
                               'list_accelerator_counts',
                               return_value=mock.MagicMock()):
            result = self.runner.invoke(command.show_gpus, ['--all'])

        assert result.exit_code == 0
        assert 'V100' in result.output

    # ==========================================================================
    # --all-regions flag tests
    # ==========================================================================

    def test_show_gpus_all_regions_flag_with_accelerator(self):
        """Test show_gpus with --all-regions flag and specific accelerator."""
        mock_aws = clouds.AWS()
        self.cloud_registry_mock.return_value = mock_aws
        self.sdk_get_mock.return_value = ['AWS']
        self.stream_and_get_mock.return_value = {}

        with mock.patch.object(sdk,
                               'list_accelerators',
                               return_value=mock.MagicMock()) as mock_list:
            result = self.runner.invoke(
                command.show_gpus, ['V100', '--cloud', 'aws', '--all-regions'])

        assert result.exit_code == 0
        call_args = mock_list.call_args
        assert call_args is not None

    # ==========================================================================
    # Disabled cloud tests
    # ==========================================================================

    def test_show_gpus_k8s_disabled_shows_message(self):
        """Test that disabled Kubernetes shows appropriate message."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus,
                                    ['--cloud', 'kubernetes'])

        assert 'Kubernetes is not enabled' in result.output

    def test_show_gpus_slurm_disabled_shows_message(self):
        """Test that disabled Slurm shows appropriate message."""
        mock_slurm = clouds.Slurm()
        self.cloud_registry_mock.return_value = mock_slurm
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus, ['--cloud', 'slurm'])

        assert 'Slurm is not enabled' in result.output

    def test_show_gpus_ssh_disabled_shows_message(self):
        """Test that disabled SSH shows appropriate message when requested."""
        mock_ssh = clouds.SSH()
        self.cloud_registry_mock.return_value = mock_ssh
        self.sdk_get_mock.return_value = []

        result = self.runner.invoke(command.show_gpus,
                                    ['A100', '--cloud', 'ssh'])

        assert 'SSH Node Pools are not enabled' in result.output or result.exit_code != 0

    # ==========================================================================
    # Case sensitivity tests
    # ==========================================================================

    def test_show_gpus_case_insensitive_accelerator(self):
        """Test that accelerator names are handled case-insensitively."""
        self.cloud_registry_mock.return_value = None
        self.sdk_get_mock.return_value = []
        self.stream_and_get_mock.return_value = {}

        with mock.patch.object(sdk,
                               'list_accelerators',
                               return_value=mock.MagicMock()) as mock_list:
            result = self.runner.invoke(command.show_gpus, ['v100'])

        assert result.exit_code == 0
        call_args = mock_list.call_args
        assert call_args is not None

    # ==========================================================================
    # Kubernetes tests
    # ==========================================================================

    def test_show_gpus_kubernetes_basic_functionality(self):
        """Test Kubernetes GPU listing with basic table structure validation."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        # Setup GPU availability
        gpu_availability = [('test-context', [('A100', [1, 2, 4], 8, 4)])]

        # Setup node info with healthy node
        mock_node_healthy = mock.MagicMock()
        mock_node_healthy.accelerator_type = 'A100'
        mock_node_healthy.total = {'accelerator_count': 4}
        mock_node_healthy.free = {'accelerators_available': 2}
        mock_node_healthy.is_ready = True
        mock_node_healthy.is_cordoned = False
        mock_node_healthy.taints = []
        mock_node_healthy.cpu_count = 96
        mock_node_healthy.cpu_free = 48
        mock_node_healthy.memory_gb = 360
        mock_node_healthy.memory_free_gb = 180

        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {'gpu-node-1': mock_node_healthy}
        mock_nodes_info.hint = None

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0

        # Validate table headers are present
        assert 'CONTEXT' in result.output
        assert 'NODE' in result.output
        assert 'vCPU' in result.output
        assert 'Memory (GB)' in result.output
        assert 'GPU UTILIZATION' in result.output
        assert 'NODE STATUS' in result.output

        # Validate data values
        assert 'test-context' in result.output
        assert 'gpu-node-1' in result.output
        assert 'A100' in result.output
        assert '48 of 96 free' in result.output  # CPU
        assert '180 of 360 free' in result.output  # Memory
        assert '2 of 4 free' in result.output  # GPU utilization
        assert 'Healthy' in result.output

    def test_show_gpus_kubernetes_with_specific_gpu_and_quantity(self):
        """Test Kubernetes with specific GPU and quantity filter."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        gpu_availability = [('context1', [('A100', [8], 16, 8)])]
        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {}
        mock_nodes_info.hint = None

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['A100:8', '--cloud', 'kubernetes'])

        assert result.exit_code == 0
        assert 'A100' in result.output

    def test_show_gpus_kubernetes_multiple_contexts(self):
        """Test Kubernetes with multiple contexts showing aggregated table with proper structure."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        # Multiple contexts with different GPU types
        gpu_availability = [
            ('prod-cluster', [('A100', [1, 2], 4, 2), ('V100', [1], 2, 1)]),
            ('staging-cluster', [('A100', [1, 2, 4], 8, 4)]),
            ('dev-cluster', [('V100', [1, 2], 4, 2)]),
        ]

        # Node info for each context
        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {}
        mock_nodes_info.hint = None

        node_info_calls = [mock_nodes_info] * 3
        self.stream_and_get_mock.side_effect = ([gpu_availability] +
                                                node_info_calls + [{}])

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0

        # Validate context names appear
        assert 'prod-cluster' in result.output
        assert 'staging-cluster' in result.output
        assert 'dev-cluster' in result.output

        # Validate GPU types from each context
        assert 'A100' in result.output
        assert 'V100' in result.output

        # Validate table sections are present
        assert 'Kubernetes' in result.output

    def test_show_gpus_kubernetes_node_status_variations(self):
        """Test Kubernetes with various node statuses: Healthy, NotReady, Cordoned, Tainted."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        gpu_availability = [('test-k8s', [('A100', [1, 2, 4], 16, 8)])]

        # Healthy node
        mock_node_healthy = mock.MagicMock()
        mock_node_healthy.accelerator_type = 'A100'
        mock_node_healthy.total = {'accelerator_count': 4}
        mock_node_healthy.free = {'accelerators_available': 2}
        mock_node_healthy.is_ready = True
        mock_node_healthy.is_cordoned = False
        mock_node_healthy.taints = []
        mock_node_healthy.cpu_count = 96
        mock_node_healthy.cpu_free = 48
        mock_node_healthy.memory_gb = 360
        mock_node_healthy.memory_free_gb = 180

        # NotReady node
        mock_node_not_ready = mock.MagicMock()
        mock_node_not_ready.accelerator_type = 'A100'
        mock_node_not_ready.total = {'accelerator_count': 4}
        mock_node_not_ready.free = {'accelerators_available': 0}
        mock_node_not_ready.is_ready = False
        mock_node_not_ready.is_cordoned = False
        mock_node_not_ready.taints = []
        mock_node_not_ready.cpu_count = 96
        mock_node_not_ready.cpu_free = None
        mock_node_not_ready.memory_gb = 360
        mock_node_not_ready.memory_free_gb = None

        # Cordoned node
        mock_node_cordoned = mock.MagicMock()
        mock_node_cordoned.accelerator_type = 'A100'
        mock_node_cordoned.total = {'accelerator_count': 4}
        mock_node_cordoned.free = {'accelerators_available': 4}
        mock_node_cordoned.is_ready = True
        mock_node_cordoned.is_cordoned = True
        mock_node_cordoned.taints = []
        mock_node_cordoned.cpu_count = 96
        mock_node_cordoned.cpu_free = 96
        mock_node_cordoned.memory_gb = 360
        mock_node_cordoned.memory_free_gb = 360

        # Tainted node with multiple taints grouped by effect
        mock_node_tainted = mock.MagicMock()
        mock_node_tainted.accelerator_type = 'A100'
        mock_node_tainted.total = {'accelerator_count': 4}
        mock_node_tainted.free = {'accelerators_available': 4}
        mock_node_tainted.is_ready = True
        mock_node_tainted.is_cordoned = False
        mock_node_tainted.taints = [{
            'key': 'nvidia.com/gpu',
            'effect': 'NoSchedule'
        }, {
            'key': 'node.kubernetes.io/memory-pressure',
            'effect': 'NoSchedule'
        }, {
            'key': 'node.kubernetes.io/disk-pressure',
            'effect': 'NoExecute'
        }]
        mock_node_tainted.cpu_count = 96
        mock_node_tainted.cpu_free = 96
        mock_node_tainted.memory_gb = 360
        mock_node_tainted.memory_free_gb = 360

        # Combined state: NotReady + Cordoned
        mock_node_combined = mock.MagicMock()
        mock_node_combined.accelerator_type = 'A100'
        mock_node_combined.total = {'accelerator_count': 4}
        mock_node_combined.free = {'accelerators_available': 0}
        mock_node_combined.is_ready = False
        mock_node_combined.is_cordoned = True
        mock_node_combined.taints = []
        mock_node_combined.cpu_count = 96
        mock_node_combined.cpu_free = None
        mock_node_combined.memory_gb = 360
        mock_node_combined.memory_free_gb = None

        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {
            'node-healthy': mock_node_healthy,
            'node-not-ready': mock_node_not_ready,
            'node-cordoned': mock_node_cordoned,
            'node-tainted': mock_node_tainted,
            'node-combined': mock_node_combined,
        }
        mock_nodes_info.hint = None

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0

        # Validate table headers
        assert 'NODE STATUS' in result.output

        # Validate node names and statuses
        assert 'node-healthy' in result.output
        assert 'node-not-ready' in result.output
        assert 'node-cordoned' in result.output
        assert 'node-tainted' in result.output
        assert 'node-combined' in result.output

        # Validate status values
        assert 'Healthy' in result.output
        assert 'NotReady' in result.output
        assert 'Cordoned' in result.output
        assert 'NoSchedule Taint' in result.output
        assert 'NoExecute Taint' in result.output

        # Validate combined status appears (NotReady, Cordoned)
        lines = result.output.split('\n')
        combined_line = [l for l in lines if 'node-combined' in l]
        assert len(combined_line) == 1
        assert 'NotReady' in combined_line[0]
        assert 'Cordoned' in combined_line[0]

    def test_show_gpus_kubernetes_edge_cases(self):
        """Test Kubernetes with edge cases: no accelerator type, zero accelerators, missing CPU/memory info."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        gpu_availability = [('test-cluster', [('A100', [1, 2], 4, 2)])]

        # Node without accelerator_type (should show as '-')
        mock_node_no_accel = mock.MagicMock()
        mock_node_no_accel.accelerator_type = None
        mock_node_no_accel.total = {'accelerator_count': 2}
        mock_node_no_accel.free = {'accelerators_available': 1}
        mock_node_no_accel.is_ready = True
        mock_node_no_accel.is_cordoned = False
        mock_node_no_accel.taints = []
        mock_node_no_accel.cpu_count = None
        mock_node_no_accel.memory_gb = None

        # Node with zero accelerators
        mock_node_zero = mock.MagicMock()
        mock_node_zero.accelerator_type = 'A100'
        mock_node_zero.total = {'accelerator_count': 0}
        mock_node_zero.free = {'accelerators_available': 0}
        mock_node_zero.is_ready = False
        mock_node_zero.is_cordoned = False
        mock_node_zero.taints = []
        mock_node_zero.cpu_count = None
        mock_node_zero.memory_gb = None

        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {
            'node-no-accel': mock_node_no_accel,
            'node-zero-gpus': mock_node_zero
        }
        mock_nodes_info.hint = None

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0

        # Validate table still renders with edge case data
        assert 'node-no-accel' in result.output
        assert 'node-zero-gpus' in result.output

        # Validate nodes show up in table (even with missing/zero data)
        lines = result.output.split('\n')
        no_accel_line = [l for l in lines if 'node-no-accel' in l]
        assert len(no_accel_line) == 1
        # Should show '-' for accelerator type
        assert '-' in no_accel_line[0]

    def test_show_gpus_kubernetes_with_hint(self):
        """Test Kubernetes node info with hint message."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        gpu_availability = [('context1', [('A100', [1, 2], 4, 2)])]

        mock_node_info = mock.MagicMock()
        mock_node_info.accelerator_type = 'A100'
        mock_node_info.total = {'accelerator_count': 2}
        mock_node_info.free = {'accelerators_available': 1}
        mock_node_info.is_ready = True
        mock_node_info.is_cordoned = False
        mock_node_info.taints = []
        mock_node_info.cpu_count = None
        mock_node_info.memory_gb = None

        mock_nodes_info = mock.MagicMock()
        mock_nodes_info.node_info_dict = {'node1': mock_node_info}
        mock_nodes_info.hint = 'Some nodes may have resource constraints'

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0
        # Verify hint message appears in output
        assert 'resource constraints' in result.output

    def test_show_gpus_kubernetes_labeled_zero_gpu_hint(self):
        """Test Kubernetes hint for nodes with GPU labels but 0 GPU resources."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        # Create a node with GPU label but 0 GPU resources
        # Use the actual model class to ensure proper structure
        mock_node_info = models.KubernetesNodeInfo(
            name='node1',
            accelerator_type='V100',
            total={'accelerator_count': 0},
            free={'accelerators_available': 0},
            ip_address=None,
            cpu_count=8.0,
            memory_gb=16.0,
            cpu_free=None,
            memory_free_gb=None,
            is_ready=True,
            is_cordoned=False,
            taints=[])

        mock_nodes_info = models.KubernetesNodesInfo(
            node_info_dict={'node1': mock_node_info}, hint='')

        # Mock the GPU availability (first call to stream_and_get)
        gpu_availability = [('context1', [('V100', [1, 2], 4, 2)])]
        # Mock the node info (second call to stream_and_get for kubernetes_node_info)
        # Mock the accelerator counts (third call to stream_and_get)
        self.stream_and_get_mock.side_effect = [
            gpu_availability,  # realtime_kubernetes_gpu_availability
            mock_nodes_info,  # kubernetes_node_info
            {},  # list_accelerator_counts
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0
        # Verify hint message appears at the bottom of output
        assert 'Some Kubernetes nodes have GPU labels but report 0 GPU' in result.output
        assert 'check the node labels and configuration' in result.output
        assert 'Affected 1 node(s): context1/node1' in result.output

    def test_show_gpus_kubernetes_labeled_zero_gpu_hint_multiple_nodes(self):
        """Test Kubernetes hint for nodes with GPU labels but 0 GPU resources."""
        mock_k8s = clouds.Kubernetes()
        self.cloud_registry_mock.return_value = mock_k8s
        self.sdk_get_mock.return_value = ['kubernetes']

        node_info_dict = {}
        for i in range(10):
            node_info_dict[f'node{i}'] = models.KubernetesNodeInfo(
                name=f'node{i}',
                accelerator_type='V100',
                total={'accelerator_count': 0},
                free={'accelerators_available': 0},
                ip_address=None,
                cpu_count=8.0,
                memory_gb=16.0,
                cpu_free=None,
                memory_free_gb=None,
                is_ready=True,
                is_cordoned=False,
                taints=[])

        mock_nodes_info = models.KubernetesNodesInfo(
            node_info_dict=node_info_dict, hint='')

        gpu_availability = [('context1', [('V100', [1, 2], 4, 2)])]

        self.stream_and_get_mock.side_effect = [
            gpu_availability,
            mock_nodes_info,
            {},
        ]

        with mock.patch.object(sdk,
                               'realtime_kubernetes_gpu_availability',
                               return_value=mock.MagicMock()):
            with mock.patch.object(sdk,
                                   'kubernetes_node_info',
                                   return_value=mock.MagicMock()):
                result = self.runner.invoke(command.show_gpus,
                                            ['--cloud', 'kubernetes'])

        assert result.exit_code == 0
        # Verify hint message appears at the bottom of output
        assert 'Some Kubernetes nodes have GPU labels but report 0 GPU' in result.output
        assert 'check the node labels and configuration' in result.output
        assert 'Affected 10 node(s): context1/node0, context1/node1, context1/node2...' in result.output
