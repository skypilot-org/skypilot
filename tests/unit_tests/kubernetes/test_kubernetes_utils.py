"""Tests for Kubernetes utils.

"""

import collections
import os
import re
import tempfile
from typing import Optional
import unittest
from unittest import mock
from unittest.mock import patch

import kubernetes
import pytest
import yaml

from sky import exceptions
from sky import models
from sky.catalog import kubernetes_catalog
from sky.provision.kubernetes import utils


# Test for exception on permanent errors like 401 (Unauthorized)
def test_get_kubernetes_nodes():
    with patch('sky.provision.kubernetes.utils.kubernetes.core_api'
              ) as mock_core_api:
        mock_core_api.return_value.list_node.side_effect = kubernetes.client.rest.ApiException(
            status=401)
        with pytest.raises(exceptions.KubeAPIUnreachableError):
            utils.get_kubernetes_nodes(context='test')


def test_get_kubernetes_node_info():
    """Tests get_kubernetes_node_info function."""
    # Mock node and pod objects
    mock_gpu_node_1 = mock.MagicMock()
    mock_gpu_node_1.metadata.name = 'node-1'
    mock_gpu_node_1.metadata.labels = {
        'skypilot.co/accelerator': 'a100-80gb',
        'cloud.google.com/gke-accelerator-count': '4'
    }
    mock_gpu_node_1.status.allocatable = {'nvidia.com/gpu': '4'}

    mock_gpu_node_2 = mock.MagicMock()
    mock_gpu_node_2.metadata.name = 'node-2'
    mock_gpu_node_2.metadata.labels = {
        'skypilot.co/accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-accelerator-count': '8',
        'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-tpu-topology': '2x4'
    }
    mock_gpu_node_2.status.allocatable = {'google.com/tpu': '8'}

    mock_pod_1 = mock.MagicMock()
    mock_pod_1.spec.node_name = 'node-1'
    mock_pod_1.status.phase = 'Running'
    mock_pod_1.spec.containers = [
        mock.MagicMock(resources=mock.MagicMock(
            requests={'nvidia.com/gpu': '2'}))
    ]

    mock_pod_2 = mock.MagicMock()
    mock_pod_2.spec.node_name = 'node-2'
    mock_pod_2.status.phase = 'Running'
    mock_pod_2.spec.containers = [
        mock.MagicMock(resources=mock.MagicMock(
            requests={'google.com/tpu': '4'}))
    ]

    # Test case 1: Normal operation with GPU and TPU nodes
    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_gpu_node_1, mock_gpu_node_2]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   return_value={mock_gpu_node_1.metadata.name: 2, mock_gpu_node_2.metadata.name: 4}), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key',
                    return_value='nvidia.com/gpu'):
        node_info = utils.get_kubernetes_node_info()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        assert len(node_info.node_info_dict) == 2
        assert node_info.node_info_dict[
            'node-1'].accelerator_type == 'A100-80GB'
        assert node_info.node_info_dict['node-1'].total[
            'accelerator_count'] == 4
        assert node_info.node_info_dict['node-1'].free[
            'accelerators_available'] == 2
        assert node_info.node_info_dict['node-2'].accelerator_type == (
            'TPU-V4-PODSLICE')
        assert node_info.node_info_dict['node-2'].total[
            'accelerator_count'] == 8
        assert node_info.node_info_dict['node-2'].free[
            'accelerators_available'] == 4

    # Test case 2: No permission to list pods
    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_gpu_node_1, mock_gpu_node_2]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   side_effect=utils.kubernetes.kubernetes.client.ApiException(
                       status=403)):
        node_info = utils.get_kubernetes_node_info()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        assert len(node_info.node_info_dict) == 2
        assert node_info.node_info_dict['node-1'].free[
            'accelerators_available'] == -1
        assert node_info.node_info_dict['node-2'].free[
            'accelerators_available'] == -1

    # Test case 3: Multi-host TPU node
    mock_tpu_node_1 = mock.MagicMock()
    mock_tpu_node_1.metadata.name = 'node-3'
    mock_tpu_node_1.metadata.labels = {
        'skypilot.co/accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-accelerator-count': '4',
        'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-tpu-topology': '4x4',
        'cloud.google.com/gke-tpu-node-pool-type': 'multi-host'
    }
    mock_tpu_node_1.status.allocatable = {'google.com/tpu': '4'}

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_gpu_node_1, mock_tpu_node_1]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   return_value=collections.defaultdict(int, {mock_gpu_node_1.metadata.name: 2})):
        node_info = utils.get_kubernetes_node_info()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        # Multi-host TPU node should be excluded
        assert len(node_info.node_info_dict) == 1
        assert 'node-3' not in node_info.node_info_dict
        assert '(Note: Multi-host TPUs are detected' in node_info.hint

    # Test case 4: Empty cluster
    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   return_value=[]):
        node_info = utils.get_kubernetes_node_info()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        assert len(node_info.node_info_dict) == 0
        assert node_info.hint == ''

    # Test case 5: CPU-only nodes
    mock_cpu_node_1 = mock.MagicMock()
    mock_cpu_node_1.metadata.name = 'node-4'
    mock_cpu_node_1.metadata.labels = {}
    mock_cpu_node_1.status.allocatable = {'cpu': '4', 'memory': '16Gi'}
    mock_cpu_node_1.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.1')
    ]

    mock_cpu_node_2 = mock.MagicMock()
    mock_cpu_node_2.metadata.name = 'node-5'
    mock_cpu_node_2.metadata.labels = {}
    mock_cpu_node_2.status.allocatable = {'cpu': '8', 'memory': '32Gi'}
    mock_cpu_node_2.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.2')
    ]

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_cpu_node_1, mock_cpu_node_2]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node') as mock_get_allocated_gpu_qty_by_node:
        node_info = utils.get_kubernetes_node_info()

        mock_get_allocated_gpu_qty_by_node.assert_not_called()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        assert len(node_info.node_info_dict) == 2
        assert node_info.node_info_dict['node-4'].accelerator_type is None
        assert node_info.node_info_dict['node-4'].total[
            'accelerator_count'] == 0
        assert node_info.node_info_dict['node-4'].free[
            'accelerators_available'] == 0
        assert node_info.node_info_dict['node-5'].total[
            'accelerator_count'] == 0
        assert node_info.node_info_dict['node-5'].free[
            'accelerators_available'] == 0

    # Test case 6: Mixed CPU and GPU nodes
    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_cpu_node_1, mock_gpu_node_1]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   return_value={mock_gpu_node_1.metadata.name: 2}) as mock_get_allocated_gpu_qty_by_node, \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key',
                   return_value='nvidia.com/gpu'):
        node_info = utils.get_kubernetes_node_info()

        mock_get_allocated_gpu_qty_by_node.assert_called_once()
        assert len(node_info.node_info_dict) == 2
        # CPU node should have 0 accelerators
        assert node_info.node_info_dict['node-4'].total[
            'accelerator_count'] == 0
        assert node_info.node_info_dict['node-4'].free[
            'accelerators_available'] == 0
        # GPU node should have correct allocation
        assert node_info.node_info_dict['node-1'].total[
            'accelerator_count'] == 4
        assert node_info.node_info_dict['node-1'].free[
            'accelerators_available'] == 2


def test_get_all_kube_context_names():
    """Tests get_all_kube_context_names function with KUBECONFIG env var."""
    mock_contexts = [{
        'name': 'context1'
    }, {
        'name': 'context2'
    }, {
        'name': 'context3'
    }]
    mock_current_context = {'name': 'context1'}

    with patch('sky.provision.kubernetes.utils.kubernetes.'
               'list_kube_config_contexts',
               return_value=(mock_contexts, mock_current_context)), \
         patch('sky.provision.kubernetes.utils.is_incluster_config_available',
               return_value=False):
        context_names = utils.get_all_kube_context_names()
        assert context_names == ['context1', 'context2', 'context3']

    with patch('sky.provision.kubernetes.utils.kubernetes.'
               'list_kube_config_contexts',
               return_value=(mock_contexts, mock_current_context)), \
         patch('sky.provision.kubernetes.utils.is_incluster_config_available',
               return_value=True), \
         patch('sky.provision.kubernetes.utils.kubernetes.'
               'in_cluster_context_name',
               return_value='in-cluster'):
        context_names = utils.get_all_kube_context_names()
        assert context_names == [
            'context1', 'context2', 'context3', 'in-cluster'
        ]

    with patch('sky.provision.kubernetes.utils.kubernetes.'
               'list_kube_config_contexts',
               side_effect=utils.kubernetes.kubernetes.config.
               config_exception.ConfigException()), \
         patch('sky.provision.kubernetes.utils.is_incluster_config_available',
               return_value=True), \
         patch('sky.provision.kubernetes.utils.kubernetes.'
               'in_cluster_context_name',
               return_value='in-cluster'):
        context_names = utils.get_all_kube_context_names()
        assert context_names == ['in-cluster']

    with patch('sky.provision.kubernetes.utils.kubernetes.'
               'list_kube_config_contexts',
               side_effect=utils.kubernetes.kubernetes.config.
               config_exception.ConfigException()), \
         patch('sky.provision.kubernetes.utils.is_incluster_config_available',
               return_value=False):
        context_names = utils.get_all_kube_context_names()
        assert context_names == []

    # Verify latest KUBECONFIG env var is used
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f1, \
         tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f2:

        # Write different kubeconfig content to each file
        kubeconfig1_content = """
apiVersion: v1
kind: Config
contexts:
- name: old-context
  context:
    cluster: old-cluster
    user: old-user
current-context: old-context
clusters:
- name: old-cluster
  cluster:
    server: https://old-server
users:
- name: old-user
  user: {}
"""
        kubeconfig2_content = """
apiVersion: v1
kind: Config
contexts:
- name: new-context
  context:
    cluster: new-cluster
    user: new-user
current-context: new-context
clusters:
- name: new-cluster
  cluster:
    server: https://new-server
users:
- name: new-user
  user: {}
"""
        f1.write(kubeconfig1_content)
        f1.flush()
        f2.write(kubeconfig2_content)
        f2.flush()

        try:
            # Mock the kubernetes module to use actual file loading
            with patch(
                    'sky.provision.kubernetes.utils.'
                    'is_incluster_config_available',
                    return_value=False):

                # Set KUBECONFIG to first file
                with patch.dict(os.environ, {'KUBECONFIG': f1.name}):
                    # Mock the kubernetes.list_kube_config_contexts to read
                    # from the actual file
                    with patch('sky.provision.kubernetes.utils.kubernetes.'
                               'list_kube_config_contexts') as mock_list:
                        # Simulate reading from first kubeconfig
                        mock_list.return_value = ([{
                            'name': 'old-context'
                        }], {
                            'name': 'old-context'
                        })
                        context_names = utils.get_all_kube_context_names()
                        assert context_names == ['old-context']
                        # Verify the function was called (indicating it tried
                        # to read config)
                        mock_list.assert_called_once()

                # Change KUBECONFIG to second file
                with patch.dict(os.environ, {'KUBECONFIG': f2.name}):
                    with patch('sky.provision.kubernetes.utils.kubernetes.'
                               'list_kube_config_contexts') as mock_list:
                        # Simulate reading from second kubeconfig
                        mock_list.return_value = ([{
                            'name': 'new-context'
                        }], {
                            'name': 'new-context'
                        })
                        context_names = utils.get_all_kube_context_names()
                        assert context_names == ['new-context']
                        mock_list.assert_called_once()

        finally:
            # Clean up temporary files
            os.unlink(f1.name)
            os.unlink(f2.name)


def test_detect_gpu_label_formatter_invalid_label_skip():
    """Tests that on finding a matching label, the
    detect_gpu_label_formatter method will skip if
    the label value is invalid."""

    # this is an invalid GKE gpu label
    valid, _ = utils.GKELabelFormatter.validate_label_value('H100_NVLINK_80GB')
    assert not valid

    # make node mocks with incorrect labels, as shown in
    # https://github.com/skypilot-org/skypilot/issues/5628
    mock_node = mock.MagicMock()
    mock_node.metadata.name = 'node'
    mock_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'H100_NVLINK_80GB',
        'gpu.nvidia.com/class': 'H100_NVLINK_80GB',
        'gpu.nvidia.com/count': '8',
        'gpu.nvidia.com/model': 'H100_NVLINK_80GB',
        'gpu.nvidia.com/vram': '81'
    }

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                    return_value=[mock_node]):
        lf, _ = utils.detect_gpu_label_formatter('whatever')
        assert lf is not None
        assert isinstance(lf, utils.CoreWeaveLabelFormatter)
        utils.detect_gpu_label_formatter.cache_clear()


def test_detect_gpu_label_formatter_suppresses_warning_for_coreweave_format():
    """Tests that warnings are not logged when GKE label keys have
    CoreWeave-formatted values (e.g., cloud.google.com/gke-accelerator=H100_NVLINK_80GB).
    This happens on CoreWeave clusters where NFD sets GKE labels but with CoreWeave values.
    """
    warning_calls = []

    def mock_warning(*args, **kwargs):
        warning_calls.append(args[0] if args else '')

    mock_node = mock.MagicMock()
    mock_node.metadata.name = 'node'
    mock_node.metadata.labels = {
        # CoreWeave clusters may have cloud.google.com/gke-accelerator labels set by Node Feature
        # Discovery (NFD), but with CoreWeave formatted values, causing confusion.
        'cloud.google.com/gke-accelerator': 'H100_NVLINK_80GB',
        'gpu.nvidia.com/class': 'H100_NVLINK_80GB',
    }

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                    return_value=[mock_node]), \
         mock.patch('sky.provision.kubernetes.utils.logger.warning', side_effect=mock_warning):
        lf, _ = utils.detect_gpu_label_formatter('whatever')

        # Should detect CoreWeaveLabelFormatter
        assert lf is not None
        assert isinstance(lf, utils.CoreWeaveLabelFormatter)

        assert len(warning_calls) == 0, (
            f'Expected no warnings about GKE label with CoreWeave format, '
            f'but got: {warning_calls}')
        utils.detect_gpu_label_formatter.cache_clear()


def test_detect_gpu_label_formatter_logs_warning_with_no_valid_labels():
    """Tests that warnings ARE logged when there are no valid labels."""
    warning_calls = []

    def mock_warning(*args, **kwargs):
        warning_calls.append(args[0] if args else '')

    mock_node = mock.MagicMock()
    mock_node.metadata.name = 'node'
    mock_node.metadata.labels = {
        # Label with invalid value for GKE formatter
        'cloud.google.com/gke-accelerator': 'H200',
    }

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                    return_value=[mock_node]), \
         mock.patch('sky.provision.kubernetes.utils.logger.warning', side_effect=mock_warning):
        utils.detect_gpu_label_formatter.cache_clear()
        lf, _ = utils.detect_gpu_label_formatter('whatever')

        # Should not detect any formatter
        assert lf is None

        # SHOULD log warning about invalid GKE label value since no valid formatter found
        expected_warning = (
            'GPU label cloud.google.com/gke-accelerator matched for label '
            'formatter GKELabelFormatter, '
            'but has invalid value H200. '
            'Reason: Invalid accelerator name in GKE cluster: H200. '
            'Skipping...')
        assert expected_warning in warning_calls, (
            f'Expected warning not found. Expected: {expected_warning!r}\n'
            f'Got warnings: {warning_calls}')


def test_detect_gpu_label_formatter_ignores_empty_labels():
    """Tests that the detect_gpu_label_formatter method correctly ignores
    empty labels from CPU nodes and selects the appropriate formatter
    based on non-empty labels from GPU nodes."""

    # Mock CPU node with empty labels (typical CPU node configuration)
    mock_cpu_node = mock.MagicMock()
    mock_cpu_node.metadata.name = 'cpu-node'
    mock_cpu_node.metadata.labels = {
        'beta.kubernetes.io/arch': 'amd64',
        'beta.kubernetes.io/os': 'linux',
        'cloud.google.com/gke-accelerator': '',  # Empty GKE label
        'gpu.nvidia.com/class': '',  # Empty CoreWeave label
        'gpu.nvidia.com/count': '',
        'gpu.nvidia.com/model': '',
        'gpu.nvidia.com/vram': ''
    }

    # Mock GPU node with mixed labels (invalid GKE, valid CoreWeave)
    mock_gpu_node = mock.MagicMock()
    mock_gpu_node.metadata.name = 'gpu-node'
    mock_gpu_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'H200',  # Invalid for GKE formatter
        'gpu.nvidia.com/class': 'H200',  # Valid for CoreWeave formatter
        'gpu.nvidia.com/count': '8',
        'gpu.nvidia.com/model': 'H200',
        'gpu.nvidia.com/vram': '143'
    }

    # Test when CPU node is returned first (Kube API is non-deterministic)
    nodes = [mock_cpu_node, mock_gpu_node]

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                    return_value=nodes):
        lf, _ = utils.detect_gpu_label_formatter('test-context')
        assert lf is not None
        assert isinstance(lf, utils.CoreWeaveLabelFormatter)
        utils.detect_gpu_label_formatter.cache_clear()

    # Test empty string variations
    mock_cpu_node_whitespace = mock.MagicMock()
    mock_cpu_node_whitespace.metadata.name = 'cpu-node-ws'
    mock_cpu_node_whitespace.metadata.labels = {
        'cloud.google.com/gke-accelerator': '   ',  # Whitespace only
        'gpu.nvidia.com/class': '\t\n',  # Whitespace only
    }

    nodes_with_whitespace = [mock_cpu_node_whitespace, mock_gpu_node]

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                    return_value=nodes_with_whitespace):
        lf, _ = utils.detect_gpu_label_formatter('test-context')
        assert lf is not None
        assert isinstance(lf, utils.CoreWeaveLabelFormatter)
        utils.detect_gpu_label_formatter.cache_clear()


# pylint: disable=line-too-long
def test_heterogenous_gpu_detection():
    """Tests that a heterogenous gpu cluster with empty
    labels are correctly processed."""

    mock_node1 = mock.MagicMock()
    mock_node1.metadata.name = 'node1'
    mock_node1.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb',
        'gpu.nvidia.com/class': 'nvidia-h100-80gb',
        'gpu.nvidia.com/count': '2',
        'gpu.nvidia.com/model': 'nvidia-h100-80gb',
        'gpu.nvidia.com/vram': '81'
    }
    mock_node1.status.allocatable = {'nvidia.com/gpu': '2'}

    mock_node2 = mock.MagicMock()
    mock_node2.metadata.name = 'node2'
    mock_node2.metadata.labels = {'cloud.google.com/gke-accelerator': ''}

    mock_container1 = mock.MagicMock()
    mock_container1.resources.requests = 0

    mock_pod1 = mock.MagicMock()
    mock_pod1.spec.node_name = 'node1'
    mock_pod1.status.phase = 'Running'
    mock_pod1.spec.containers = [mock_container1]

    mock_container2 = mock.MagicMock()
    mock_container2.resources.requests = 0

    mock_pod2 = mock.MagicMock()
    mock_pod2.spec.node_name = 'node2'
    mock_pod2.status.phase = 'Running'
    mock_pod2.spec.containers = [mock_container2]

    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name', return_value='doesntexist'), \
         mock.patch('sky.provision.kubernetes.utils.check_credentials', return_value=[True]), \
         mock.patch('sky.provision.kubernetes.utils.detect_accelerator_resource', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.detect_gpu_label_formatter', return_value=[utils.GKELabelFormatter(), None]), \
         mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes', return_value=[mock_node1, mock_node2]), \
         mock.patch('sky.provision.kubernetes.utils.get_allocated_gpu_qty_by_node', return_value={mock_node1.metadata.name: 1, mock_node2.metadata.name: 0}), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key', return_value='nvidia.com/gpu'):

        counts, capacity, available = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)
        assert (set(counts.keys()) == set(capacity.keys()) == set(available.keys())), \
            (f'Keys of counts ({list(counts.keys())}), capacity ({list(capacity.keys())}), '
             f'and available ({list(available.keys())}) must be the same.')
        assert available == {'H100': 1}


def test_low_priority_pod_filtering():
    """Tests that low priority pods (e.g., CoreWeave HPC verification) are excluded from GPU allocation calculations."""
    # Mock node with 8 GPUs
    mock_node = mock.MagicMock()
    mock_node.metadata.name = 'gpu-node'
    mock_node.metadata.labels = {
        'skypilot.co/accelerator': 'h100-80gb',
        'cloud.google.com/gke-accelerator-count': '8'
    }
    mock_node.status.allocatable = {'nvidia.com/gpu': '8'}
    mock_node.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.1')
    ]

    # Mock regular pod requesting 2 GPUs
    mock_regular_pod = mock.MagicMock()
    mock_regular_pod.spec.node_name = 'gpu-node'
    mock_regular_pod.status.phase = 'Running'
    mock_regular_pod.metadata.name = 'regular-workload-pod'
    mock_regular_pod.metadata.namespace = 'default'
    mock_regular_pod.spec.containers = [
        mock.MagicMock(resources=mock.MagicMock(
            requests={'nvidia.com/gpu': '2'}))
    ]

    # Mock low priority pod requesting 4 GPUs (should be excluded)
    mock_low_priority_pod = mock.MagicMock()
    mock_low_priority_pod.spec.node_name = 'gpu-node'
    mock_low_priority_pod.status.phase = 'Running'
    mock_low_priority_pod.metadata.name = 'hpc-verification-h100-80gb-test'
    mock_low_priority_pod.metadata.namespace = 'cw-hpc-verification'
    mock_low_priority_pod.spec.containers = [
        mock.MagicMock(resources=mock.MagicMock(
            requests={'nvidia.com/gpu': '4'}))
    ]

    # Test with low priority pod filtering
    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_node]), \
         mock.patch('sky.provision.kubernetes.utils.'
                   'get_allocated_gpu_qty_by_node',
                   return_value={mock_node.metadata.name: 2}), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key',
                    return_value='nvidia.com/gpu'):

        node_info = utils.get_kubernetes_node_info()
        assert isinstance(node_info, models.KubernetesNodesInfo)
        assert len(node_info.node_info_dict) == 1
        # Should have 8 total GPUs, 2 allocated (regular pod only), 6 available
        # Low priority pod should be excluded from allocation calculations
        assert node_info.node_info_dict['gpu-node'].total[
            'accelerator_count'] == 8
        assert node_info.node_info_dict['gpu-node'].free[
            'accelerators_available'] == 6


def test_should_exclude_pod_from_gpu_allocation():
    """Tests the helper function that identifies pods to exclude from GPU allocation calculations."""
    # Test CoreWeave HPC verification pod (should be excluded)
    mock_hpc_pod = mock.MagicMock()
    mock_hpc_pod.metadata.namespace = 'cw-hpc-verification'

    # Test regular pod (should not be excluded)
    mock_regular_pod = mock.MagicMock()
    mock_regular_pod.metadata.namespace = 'default'

    # Test pod with different namespace (should not be excluded)
    mock_other_pod = mock.MagicMock()
    mock_other_pod.metadata.namespace = 'some-other-namespace'

    # CoreWeave HPC verification pod should be excluded
    assert utils.should_exclude_pod_from_gpu_allocation(mock_hpc_pod) == True

    # Regular pods should not be excluded
    assert utils.should_exclude_pod_from_gpu_allocation(
        mock_regular_pod) == False
    assert utils.should_exclude_pod_from_gpu_allocation(mock_other_pod) == False


class TestCheckPodConfig(unittest.TestCase):
    """Unit tests for check_pod_config."""

    # apiVersion: v1 and kind: Pod are not required, since
    # we are just validating the metadata and spec fields.
    MINIMAL_POD_BASE = '''
metadata:
  name: test-pod
spec:
  containers:
  - name: default-container
    image: alpine:latest'''

    def _check_pod_config(self,
                          yaml_string: str,
                          expected_is_valid: bool,
                          expected_error_msg: Optional[str] = None):
        """Helper method to test pod config validation.

        Args:
            yaml_string: YAML string to validate
            expected_is_valid: Expected validation result
            expected_error_msg: Expected error message (if any). If provided, checks that this string is contained in the error message.
        """
        pod_dict = yaml.safe_load(yaml_string)
        is_valid, error_msg = utils.check_pod_config(pod_dict)
        assert is_valid == expected_is_valid

        if expected_is_valid:
            assert error_msg is None
        else:
            assert error_msg is not None
            if expected_error_msg:
                assert expected_error_msg in error_msg

    def test_minimal_valid_pod(self):
        """Test with minimal valid pod configuration."""
        self._check_pod_config(self.MINIMAL_POD_BASE, True)

    def test_invalid_pod_spec_field_name(self):
        """Test with invalid pod spec field names."""
        # Potential user typo: volume instead of volumes
        invalid_pod_spec_field = f'''
{self.MINIMAL_POD_BASE}
  volume: some-value'''

        self._check_pod_config(
            invalid_pod_spec_field,
            False,
            expected_error_msg='Validation error in spec.volume: Unknown field')

    def test_invalid_container_spec_field_name(self):
        """Test with invalid container field names."""
        # Potential user typo: arg instead of args
        invalid_container_field = f'''
{self.MINIMAL_POD_BASE}
    arg: ['hello']'''

        self._check_pod_config(
            invalid_container_field,
            False,
            expected_error_msg=
            'Validation error in spec.containers.arg: Unknown field')

    def test_invalid_metadata_field_name(self):
        """Test with invalid metadata field names."""
        invalid_metadata_field = '''
metadata:
  name: invalid-metadata-pod
  invalidMetadataField: invalid-value
spec:
  containers:
  - name: container
    image: alpine:latest'''

        self._check_pod_config(
            invalid_metadata_field,
            False,
            expected_error_msg=
            'Validation error in metadata.invalidMetadataField: Unknown field')

    def test_missing_required_container_name(self):
        """Test with missing required container name."""
        # `name` is required by V1Container
        container_without_name = '''
spec:
  containers:
  - image: alpine:latest
'''

        self._check_pod_config(
            container_without_name,
            False,
            expected_error_msg=
            'Validation error in spec.containers: Invalid value for `name`')

    def test_missing_required_volume_name(self):
        """Test with missing required volume name."""
        # `name` is required by V1Volume
        volume_without_name = f'''
{self.MINIMAL_POD_BASE}
  volumes:
  - persistentVolumeClaim:
      claimName: my-pvc
'''

        self._check_pod_config(
            volume_without_name,
            False,
            expected_error_msg=
            'Validation error in spec.volumes: Invalid value for `name`')

    def test_missing_required_container_env_name(self):
        """Test with missing required container env name."""
        # `name` is required by V1EnvVar
        container_without_env_name = f'''
{self.MINIMAL_POD_BASE}
spec:
  containers:
  - name: default-container
    image: alpine:latest
    env:
    - value: some-value
'''

        self._check_pod_config(
            container_without_env_name,
            False,
            expected_error_msg=
            'Validation error in spec.containers.env: Invalid value for `name`')

    def test_missing_optional_container_image(self):
        """Test with missing optional container image."""
        # `image` is optional in V1Container
        container_without_image = '''
spec:
  containers:
  - name: test-container'''
        self._check_pod_config(container_without_image, True)

    def test_spec_without_containers(self):
        """Test with spec without containers."""
        spec_without_containers = '''
spec: {}'''

        self._check_pod_config(
            spec_without_containers,
            False,
            expected_error_msg=
            'Validation error in spec: Invalid value for `containers`')

    def test_valid_field_invalid_type(self):
        """Test with valid field but invalid type.

        This won't be caught by the client-side validation, but will be
        caught later on by the Kubernetes API server in run_instances.
        """
        valid_field_invalid_type = f'''
{self.MINIMAL_POD_BASE}
    readinessProbe: hello
'''
        self._check_pod_config(valid_field_invalid_type, True)

    def test_comprehensive_pod_features(self):
        """Test pod with comprehensive Kubernetes features."""
        comprehensive_pod_config = '''
metadata:
  name: comprehensive-test-pod
  namespace: test-namespace
  labels:
    app: comprehensive-app
    version: v2.0
    tier: backend
  annotations:
    description: "Comprehensive pod testing all Kubernetes features"
    scheduler.alpha.kubernetes.io/critical-pod: ""
    container.apparmor.security.beta.kubernetes.io/gpu-container: runtime/default
    k8s.v1.cni.cncf.io/networks: macvlan-conf
spec:
  restartPolicy: OnFailure
  terminationGracePeriodSeconds: 60
  activeDeadlineSeconds: 3600
  priorityClassName: high-priority
  priority: 1000
  serviceAccountName: comprehensive-service-account
  automountServiceAccountToken: true
  hostNetwork: false
  hostPID: false
  hostIPC: false
  dnsPolicy: ClusterFirst
  enableServiceLinks: false
  shareProcessNamespace: false
  dnsConfig:
    nameservers:
    - 8.8.8.8
    - 8.8.4.4
    searches:
    - example.com
    - cluster.local
    options:
    - name: ndots
      value: "2"
  hostAliases:
  - ip: "192.168.1.100"
    hostnames:
    - "example.com"
  imagePullSecrets:
  - name: registry-secret-1
  - name: registry-secret-2
  securityContext:
    runAsUser: 1000
    runAsGroup: 3000
    runAsNonRoot: true
    fsGroup: 2000
    supplementalGroups: [4000, 5000]
    seLinuxOptions:
      level: "s0:c123,c456"
    sysctls:
    - name: net.core.somaxconn
      value: "1024"
  nodeSelector:
    kubernetes.io/arch: amd64
    node-type: gpu-enabled
  tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
  - key: dedicated
    operator: Equal
    value: "true"
    effect: NoExecute
    tolerationSeconds: 300
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: kubernetes.io/arch
            operator: In
            values: ["amd64", "arm64"]
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
          - key: node-type
            operator: In
            values: ["gpu-enabled"]
    podAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchLabels:
            app: database
        topologyKey: kubernetes.io/hostname
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 50
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: web-server
          topologyKey: kubernetes.io/hostname
  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app: comprehensive-app
  initContainers:
  - name: init-setup
    image: busybox:latest
    command: ['sh', '-c', 'echo "Initializing..." && sleep 5']
    securityContext:
      runAsUser: 1001
      allowPrivilegeEscalation: false
  - name: init-download
    image: curlimages/curl:latest
    command: ['curl', '-o', '/shared/config.json', 'https://example.com/config']
    volumeMounts:
    - name: shared-data
      mountPath: /shared
  containers:
  - name: gpu-app-container
    image: nvidia/cuda:11.8-devel-ubuntu20.04
    imagePullPolicy: Always
    workingDir: /app
    command: ["python", "/app/main.py"]
    args: ["-c", "/config/app.conf"]
    ports:
    - name: http
      containerPort: 8080
      protocol: TCP
    - name: metrics
      containerPort: 9090
      protocol: TCP
    resources:
      requests:
        nvidia.com/gpu: "2"
        cpu: "2"
        memory: "4Gi"
        ephemeral-storage: "10Gi"
        hugepages-2Mi: "1Gi"
      limits:
        nvidia.com/gpu: "2"
        cpu: "4"
        memory: "8Gi"
        ephemeral-storage: "20Gi"
        hugepages-2Mi: "1Gi"
    env:
    - name: CUDA_VISIBLE_DEVICES
      value: "0,1"
    - name: POD_NAME
      valueFrom:
        fieldRef:
          fieldPath: metadata.name
    - name: POD_NAMESPACE
      valueFrom:
        fieldRef:
          fieldPath: metadata.namespace
    - name: NODE_NAME
      valueFrom:
        fieldRef:
          fieldPath: spec.nodeName
    - name: CONFIG_VALUE
      valueFrom:
        configMapKeyRef:
          name: app-config
          key: config-key
          optional: true
    - name: SECRET_VALUE
      valueFrom:
        secretKeyRef:
          name: app-secrets
          key: secret-key
    - name: RESOURCE_LIMIT_CPU
      valueFrom:
        resourceFieldRef:
          resource: limits.cpu
    envFrom:
    - configMapRef:
        name: env-config
        optional: true
    - secretRef:
        name: env-secrets
    - prefix: DB_
      configMapRef:
        name: database-config
    securityContext:
      runAsUser: 1001
      runAsGroup: 3001
      runAsNonRoot: true
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        add: ["NET_ADMIN", "SYS_TIME"]
        drop: ["ALL"]
      seccompProfile:
        type: RuntimeDefault
      procMount: Default
    lifecycle:
      postStart:
        exec:
          command: ["/bin/sh", "-c", "echo 'Container started' > /var/log/startup.log"]
      preStop:
        httpGet:
          path: /shutdown
          port: 8080
          scheme: HTTP
    livenessProbe:
      httpGet:
        path: /health
        port: 8080
        httpHeaders:
        - name: Custom-Header
          value: health-check
      initialDelaySeconds: 30
      periodSeconds: 10
      timeoutSeconds: 5
      successThreshold: 1
      failureThreshold: 3
    readinessProbe:
      tcpSocket:
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 5
      timeoutSeconds: 3
      successThreshold: 1
      failureThreshold: 3
    startupProbe:
      exec:
        command: ["cat", "/tmp/healthy"]
      initialDelaySeconds: 10
      periodSeconds: 10
      timeoutSeconds: 1
      successThreshold: 1
      failureThreshold: 30
    volumeMounts:
    - name: pvc-volume
      mountPath: /data
    - name: config-volume
      mountPath: /config
      readOnly: true
    - name: secret-volume
      mountPath: /secrets
      readOnly: true
    - name: shared-data
      mountPath: /shared
    - name: empty-volume
      mountPath: /tmp-data
    - name: projected-volume
      mountPath: /projected
      subPath: configs
    - name: writable-volume
      mountPath: /writable
    volumeDevices:
    - name: block-volume
      devicePath: /dev/block-device
  - name: tpu-sidecar
    image: tensorflow/tensorflow:latest
    resources:
      requests:
        google.com/tpu: "1"
        cpu: "1"
        memory: "2Gi"
      limits:
        google.com/tpu: "1"
        cpu: "2"
        memory: "4Gi"
    volumeMounts:
    - name: log-volume
      mountPath: /var/log
  volumes:
  - name: pvc-volume
    persistentVolumeClaim:
      claimName: my-pvc
      readOnly: false
  - name: config-volume
    configMap:
      name: app-config
      defaultMode: 0644
      items:
      - key: config.yaml
        path: app-config.yaml
        mode: 0600
  - name: secret-volume
    secret:
      secretName: app-secrets
      defaultMode: 0400
  - name: host-volume
    hostPath:
      path: /host/data
      type: DirectoryOrCreate
  - name: empty-volume
    emptyDir:
      sizeLimit: "2Gi"
      medium: Memory
  - name: shared-data
    emptyDir: {}
  - name: log-volume
    emptyDir: {}
  - name: writable-volume
    emptyDir: {}
  - name: projected-volume
    projected:
      defaultMode: 0644
      sources:
      - configMap:
          name: config1
      - secret:
          name: secret1
      - serviceAccountToken:
          path: token
          expirationSeconds: 3600
  - name: block-volume
    persistentVolumeClaim:
      claimName: block-pvc
'''

        self._check_pod_config(comprehensive_pod_config, True)


def test_parse_cpu_or_gpu_resource_to_float():
    """Test parse_cpu_or_gpu_resource_to_float function."""
    # Test with millicore values (ending with 'm')
    assert utils.parse_cpu_or_gpu_resource_to_float('500m') == 0.5
    assert utils.parse_cpu_or_gpu_resource_to_float('1000m') == 1.0
    assert utils.parse_cpu_or_gpu_resource_to_float('250m') == 0.25
    assert utils.parse_cpu_or_gpu_resource_to_float('1m') == 0.001
    assert utils.parse_cpu_or_gpu_resource_to_float('0m') == 0.0

    # Test with whole number values (no 'm' suffix)
    assert utils.parse_cpu_or_gpu_resource_to_float('1') == 1.0
    assert utils.parse_cpu_or_gpu_resource_to_float('2') == 2.0
    assert utils.parse_cpu_or_gpu_resource_to_float('0') == 0.0
    assert utils.parse_cpu_or_gpu_resource_to_float('4.5') == 4.5
    assert utils.parse_cpu_or_gpu_resource_to_float('0.5') == 0.5

    # Test edge cases
    assert utils.parse_cpu_or_gpu_resource_to_float('') == 0.0  # Empty string


def test_coreweave_autoscaler():
    """Test that CoreweaveAutoscaler is properly configured."""
    from sky.provision.kubernetes.utils import AUTOSCALER_TYPE_TO_AUTOSCALER
    from sky.provision.kubernetes.utils import CoreweaveAutoscaler
    from sky.provision.kubernetes.utils import CoreWeaveLabelFormatter
    from sky.utils import kubernetes_enums

    # Test that COREWEAVE autoscaler type is mapped correctly
    autoscaler_class = AUTOSCALER_TYPE_TO_AUTOSCALER.get(
        kubernetes_enums.KubernetesAutoscalerType.COREWEAVE)
    assert autoscaler_class is not None
    assert autoscaler_class == CoreweaveAutoscaler

    # Test that CoreweaveAutoscaler uses the correct label formatter
    assert CoreweaveAutoscaler.label_formatter == CoreWeaveLabelFormatter

    # Test that CoreweaveAutoscaler cannot query backend (like other simple autoscalers)
    assert CoreweaveAutoscaler.can_query_backend == False


def test_combine_pod_config_fields_ssh_cloud():
    """Test combine_pod_config_fields with SSH cloud and context handling."""
    from sky import clouds
    from sky.utils import config_utils

    # Create a basic cluster YAML object
    cluster_yaml_obj = {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'spec': {
                        'containers': [{
                            'name': 'ray',
                            'image': 'rayproject/ray:nightly'
                        }]
                    }
                }
            }
        }
    }

    # Test 1: SSH cloud without context
    ssh_cloud = clouds.SSH()
    cluster_config_overrides = {}

    with patch('sky.skypilot_config.get_effective_region_config',
               return_value={}):
        result = utils.combine_pod_config_fields(cluster_yaml_obj,
                                                 cluster_config_overrides,
                                                 cloud=ssh_cloud)
        assert result is not None
        assert 'available_node_types' in result

    # Test 2: SSH cloud with context (should strip "ssh-" prefix)
    ssh_context = 'ssh-my-cluster'
    pod_config_for_context = {
        'spec': {
            'imagePullSecrets': [{
                'name': 'my-secret'
            }]
        }
    }

    with patch('sky.skypilot_config.get_effective_region_config'
              ) as mock_get_config:
        mock_get_config.return_value = pod_config_for_context
        result = utils.combine_pod_config_fields(cluster_yaml_obj,
                                                 cluster_config_overrides,
                                                 cloud=ssh_cloud,
                                                 context=ssh_context)

        # Verify that get_effective_region_config was called with 'ssh' cloud
        # and context without the "ssh-" prefix
        mock_get_config.assert_called_once_with(cloud='ssh',
                                                region='my-cluster',
                                                keys=('pod_config',),
                                                default_value={})

        # Verify the pod config was merged
        node_config = result['available_node_types']['ray_head_default'][
            'node_config']
        assert 'imagePullSecrets' in node_config['spec']
        assert node_config['spec']['imagePullSecrets'][0]['name'] == 'my-secret'

    # Test 3: SSH cloud with context that doesn't start with "ssh-" should raise assertion
    invalid_context = 'my-cluster'
    with pytest.raises(AssertionError,
                       match='SSH context must start with "ssh-"'):
        utils.combine_pod_config_fields(cluster_yaml_obj,
                                        cluster_config_overrides,
                                        cloud=ssh_cloud,
                                        context=invalid_context)


def test_combine_pod_config_fields_kubernetes_cloud():
    """Test combine_pod_config_fields with Kubernetes cloud."""
    from sky import clouds

    # Create a basic cluster YAML object
    cluster_yaml_obj = {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'spec': {
                        'containers': [{
                            'name': 'ray',
                            'image': 'rayproject/ray:nightly'
                        }]
                    }
                }
            }
        }
    }

    # Test with Kubernetes cloud and context
    k8s_cloud = clouds.Kubernetes()
    k8s_context = 'my-k8s-cluster'
    cluster_config_overrides = {}
    pod_config_for_context = {'spec': {'nodeSelector': {'gpu': 'true'}}}

    with patch('sky.skypilot_config.get_effective_region_config'
              ) as mock_get_config:
        mock_get_config.return_value = pod_config_for_context
        result = utils.combine_pod_config_fields(cluster_yaml_obj,
                                                 cluster_config_overrides,
                                                 cloud=k8s_cloud,
                                                 context=k8s_context)

        # Verify that get_effective_region_config was called with 'kubernetes' cloud
        # and the context as-is
        mock_get_config.assert_called_once_with(cloud='kubernetes',
                                                region=k8s_context,
                                                keys=('pod_config',),
                                                default_value={})

        # Verify the pod config was merged
        node_config = result['available_node_types']['ray_head_default'][
            'node_config']
        assert 'nodeSelector' in node_config['spec']
        assert node_config['spec']['nodeSelector']['gpu'] == 'true'


def test_ssh_cloud_uses_ssh_config_for_provision_timeout():
    """Test that SSH cloud uses 'ssh' cloud name for provision_timeout config lookup."""
    from sky import clouds
    from sky.utils import config_utils

    # Create SSH and Kubernetes cloud instances
    ssh_cloud = clouds.SSH()
    k8s_cloud = clouds.Kubernetes()

    # Verify that _REPR is set correctly
    assert ssh_cloud._REPR == 'SSH'
    assert k8s_cloud._REPR == 'Kubernetes'

    # Create a config dictionary with both ssh and kubernetes provision_timeout
    config_dict = {
        'ssh': {
            'provision_timeout': 7200,
            'context_configs': {
                'my-cluster': {
                    'provision_timeout': 9000
                }
            }
        },
        'kubernetes': {
            'provision_timeout': 3600,
            'context_configs': {
                'k8s-cluster': {
                    'provision_timeout': 5400
                }
            }
        }
    }

    # Test SSH cloud retrieves from 'ssh' config
    ssh_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region=None,
        keys=('provision_timeout',),
        default_value=600)
    assert ssh_timeout == 7200

    # Test SSH cloud retrieves context-specific timeout
    ssh_context_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region='my-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert ssh_context_timeout == 9000

    # Test Kubernetes cloud retrieves from 'kubernetes' config
    k8s_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region=None,
        keys=('provision_timeout',),
        default_value=600)
    assert k8s_timeout == 3600

    # Test Kubernetes cloud retrieves context-specific timeout
    k8s_context_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region='k8s-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert k8s_context_timeout == 5400


def test_ssh_cloud_context_stripping():
    """Test that SSH cloud contexts have 'ssh-' prefix stripped when looking up config."""
    from sky import clouds
    from sky.utils import config_utils

    ssh_cloud = clouds.SSH()

    # SSH contexts are prefixed with 'ssh-', but the config uses the name without prefix
    ssh_context = 'ssh-my-cluster'
    expected_config_key = 'my-cluster'

    config_dict = {
        'ssh': {
            'context_configs': {
                'my-cluster': {  # Config uses name without 'ssh-' prefix
                    'provision_timeout': 9000,
                    'pod_config': {
                        'metadata': {
                            'labels': {
                                'team': 'ml'
                            }
                        }
                    }
                }
            }
        }
    }

    # When combine_pod_config_fields receives 'ssh-my-cluster' context,
    # it should strip the 'ssh-' prefix and look up 'my-cluster' in the config
    timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region=expected_config_key,  # Uses stripped name
        keys=('provision_timeout',),
        default_value=600)
    assert timeout == 9000

    pod_config = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region=expected_config_key,
        keys=('pod_config',),
        default_value={})
    assert pod_config == {'metadata': {'labels': {'team': 'ml'}}}


def test_ssh_config_does_not_leak_to_kubernetes():
    """Test that SSH pod_config does not leak to Kubernetes cloud."""
    from sky import clouds
    from sky.utils import config_utils

    # Config with both SSH and Kubernetes sections
    config_dict = {
        'ssh': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'source': 'ssh-config',
                        'ssh-only': 'true'
                    }
                }
            },
            'provision_timeout': 7200
        },
        'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'source': 'kubernetes-config',
                        'k8s-only': 'true'
                    }
                }
            },
            'provision_timeout': 3600
        }
    }

    # Kubernetes cloud should ONLY get kubernetes config
    k8s_pod_config = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region=None,
        keys=('pod_config',),
        default_value={})

    # Verify it got kubernetes config, NOT ssh config
    assert k8s_pod_config['metadata']['labels']['source'] == 'kubernetes-config'
    assert 'k8s-only' in k8s_pod_config['metadata']['labels']
    assert 'ssh-only' not in k8s_pod_config['metadata']['labels'], \
        "SSH config should NOT leak to Kubernetes"

    # Kubernetes provision_timeout should be from kubernetes config
    k8s_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region=None,
        keys=('provision_timeout',),
        default_value=600)
    assert k8s_timeout == 3600, "Should get Kubernetes timeout, not SSH timeout"


def test_kubernetes_config_does_not_leak_to_ssh():
    """Test that Kubernetes pod_config does not leak to SSH cloud."""
    from sky import clouds
    from sky.utils import config_utils

    # Config with both SSH and Kubernetes sections
    config_dict = {
        'ssh': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'source': 'ssh-config',
                        'ssh-only': 'true'
                    }
                }
            },
            'provision_timeout': 7200
        },
        'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'source': 'kubernetes-config',
                        'k8s-only': 'true'
                    }
                }
            },
            'provision_timeout': 3600
        }
    }

    # SSH cloud should ONLY get ssh config
    ssh_pod_config = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region=None,
        keys=('pod_config',),
        default_value={})

    # Verify it got ssh config, NOT kubernetes config
    assert ssh_pod_config['metadata']['labels']['source'] == 'ssh-config'
    assert 'ssh-only' in ssh_pod_config['metadata']['labels']
    assert 'k8s-only' not in ssh_pod_config['metadata']['labels'], \
        "Kubernetes config should NOT leak to SSH"

    # SSH provision_timeout should be from ssh config
    ssh_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region=None,
        keys=('provision_timeout',),
        default_value=600)
    assert ssh_timeout == 7200, "Should get SSH timeout, not Kubernetes timeout"


def test_ssh_and_kubernetes_context_configs_isolated():
    """Test that SSH and Kubernetes context configs are completely isolated."""
    from sky import clouds
    from sky.utils import config_utils

    # Config with context_configs for both clouds
    config_dict = {
        'ssh': {
            'context_configs': {
                'my-cluster': {
                    'pod_config': {
                        'metadata': {
                            'labels': {
                                'from': 'ssh-context-config'
                            }
                        }
                    },
                    'provision_timeout': 9000
                }
            }
        },
        'kubernetes': {
            'context_configs': {
                'my-cluster': {  # Same context name as SSH
                    'pod_config': {
                        'metadata': {
                            'labels': {
                                'from': 'k8s-context-config'
                            }
                        }
                    },
                    'provision_timeout': 5400
                }
            }
        }
    }

    # SSH should only get SSH context config
    ssh_pod_config = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region='my-cluster',
        keys=('pod_config',),
        default_value={})
    assert ssh_pod_config['metadata']['labels']['from'] == 'ssh-context-config', \
        "SSH should get SSH context config"
    assert ssh_pod_config['metadata']['labels']['from'] != 'k8s-context-config', \
        "SSH should NOT get Kubernetes context config (no leakage)"

    ssh_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region='my-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert ssh_timeout == 9000, \
        "SSH should get SSH provision_timeout"
    assert ssh_timeout != 5400, \
        "SSH should NOT get Kubernetes provision_timeout (no leakage)"

    # Kubernetes should only get Kubernetes context config
    k8s_pod_config = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region='my-cluster',
        keys=('pod_config',),
        default_value={})
    assert k8s_pod_config['metadata']['labels']['from'] == 'k8s-context-config', \
        "Kubernetes should get Kubernetes context config"
    assert k8s_pod_config['metadata']['labels']['from'] != 'ssh-context-config', \
        "Kubernetes should NOT get SSH context config (no leakage)"

    k8s_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region='my-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert k8s_timeout == 5400, \
        "Kubernetes should get Kubernetes provision_timeout"
    assert k8s_timeout != 9000, \
        "Kubernetes should NOT get SSH provision_timeout (no leakage)"


def test_context_configs_no_leakage_between_ssh_and_kubernetes():
    """Test that context_configs with same context name don't leak across clouds.

    This is a critical test ensuring that when both SSH and Kubernetes have
    context_configs for the same context name (e.g., 'my-cluster'), they are
    completely isolated and don't leak into each other.
    """
    from sky.utils import config_utils

    # Both SSH and Kubernetes have context_configs for 'my-cluster'
    # with DIFFERENT values that should NOT leak
    config_dict = {
        'ssh': {
            'context_configs': {
                'my-cluster': {
                    'pod_config': {
                        'metadata': {
                            'labels': {
                                'cloud': 'ssh',
                                'ssh-only-label': 'ssh-value'
                            }
                        }
                    },
                    'provision_timeout': 7200
                }
            }
        },
        'kubernetes': {
            'context_configs': {
                'my-cluster': {  # SAME context name!
                    'pod_config': {
                        'metadata': {
                            'labels': {
                                'cloud': 'kubernetes',
                                'k8s-only-label': 'k8s-value'
                            }
                        }
                    },
                    'provision_timeout': 3600
                }
            }
        }
    }

    # Test SSH lookup for 'my-cluster'
    ssh_result = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region='my-cluster',
        keys=('pod_config',),
        default_value={})

    # SSH should have SSH labels
    assert ssh_result['metadata']['labels']['cloud'] == 'ssh'
    assert 'ssh-only-label' in ssh_result['metadata']['labels']
    assert ssh_result['metadata']['labels']['ssh-only-label'] == 'ssh-value'

    # SSH should NOT have Kubernetes labels (no leakage)
    assert 'k8s-only-label' not in ssh_result['metadata']['labels'], \
        "Kubernetes labels should NOT leak into SSH context_configs"
    assert ssh_result['metadata']['labels']['cloud'] != 'kubernetes', \
        "Kubernetes cloud label should NOT leak into SSH context_configs"

    # Test Kubernetes lookup for 'my-cluster'
    k8s_result = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region='my-cluster',
        keys=('pod_config',),
        default_value={})

    # Kubernetes should have Kubernetes labels
    assert k8s_result['metadata']['labels']['cloud'] == 'kubernetes'
    assert 'k8s-only-label' in k8s_result['metadata']['labels']
    assert k8s_result['metadata']['labels']['k8s-only-label'] == 'k8s-value'

    # Kubernetes should NOT have SSH labels (no leakage)
    assert 'ssh-only-label' not in k8s_result['metadata']['labels'], \
        "SSH labels should NOT leak into Kubernetes context_configs"
    assert k8s_result['metadata']['labels']['cloud'] != 'ssh', \
        "SSH cloud label should NOT leak into Kubernetes context_configs"

    # Test provision_timeout isolation
    ssh_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='ssh',
        region='my-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert ssh_timeout == 7200, "SSH should get SSH timeout"
    assert ssh_timeout != 3600, "SSH should NOT get Kubernetes timeout"

    k8s_timeout = config_utils.get_cloud_config_value_from_dict(
        dict_config=config_dict,
        cloud='kubernetes',
        region='my-cluster',
        keys=('provision_timeout',),
        default_value=600)
    assert k8s_timeout == 3600, "Kubernetes should get Kubernetes timeout"
    assert k8s_timeout != 7200, "Kubernetes should NOT get SSH timeout"


def test_combine_pod_config_fields_ssh_and_kubernetes_isolation():
    """Test that combine_pod_config_fields maintains isolation between SSH and Kubernetes."""
    from unittest.mock import patch

    from sky import clouds

    # Create basic cluster YAML
    cluster_yaml_obj = {
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'spec': {
                        'containers': [{
                            'name': 'ray',
                            'image': 'rayproject/ray:nightly'
                        }]
                    }
                }
            }
        }
    }

    # SSH cloud with SSH context
    ssh_cloud = clouds.SSH()
    ssh_context = 'ssh-test-cluster'

    # Kubernetes cloud with regular context
    k8s_cloud = clouds.Kubernetes()
    k8s_context = 'k8s-test-cluster'

    # Mock config that has DIFFERENT pod_configs for SSH vs Kubernetes
    ssh_pod_config = {'spec': {'nodeSelector': {'ssh-node': 'true'}}}
    k8s_pod_config = {'spec': {'nodeSelector': {'k8s-node': 'true'}}}

    # Test SSH cloud gets SSH config
    with patch('sky.skypilot_config.get_effective_region_config'
              ) as mock_get_config:
        mock_get_config.return_value = ssh_pod_config

        result = utils.combine_pod_config_fields(cluster_yaml_obj, {},
                                                 cloud=ssh_cloud,
                                                 context=ssh_context)

        # Verify SSH pod config was used
        node_config = result['available_node_types']['ray_head_default'][
            'node_config']
        assert 'nodeSelector' in node_config['spec']
        assert node_config['spec']['nodeSelector']['ssh-node'] == 'true'
        assert 'k8s-node' not in node_config['spec']['nodeSelector'], \
            "Kubernetes config leaked to SSH!"

        # Verify get_effective_region_config was called with 'ssh'
        mock_get_config.assert_called_with(cloud='ssh',
                                           region='test-cluster',
                                           keys=('pod_config',),
                                           default_value={})

    # Test Kubernetes cloud gets Kubernetes config
    with patch('sky.skypilot_config.get_effective_region_config'
              ) as mock_get_config:
        mock_get_config.return_value = k8s_pod_config

        result = utils.combine_pod_config_fields(cluster_yaml_obj, {},
                                                 cloud=k8s_cloud,
                                                 context=k8s_context)

        # Verify Kubernetes pod config was used
        node_config = result['available_node_types']['ray_head_default'][
            'node_config']
        assert 'nodeSelector' in node_config['spec']
        assert node_config['spec']['nodeSelector']['k8s-node'] == 'true'
        assert 'ssh-node' not in node_config['spec']['nodeSelector'], \
            "SSH config leaked to Kubernetes!"

        # Verify get_effective_region_config was called with 'kubernetes'
        mock_get_config.assert_called_with(cloud='kubernetes',
                                           region=k8s_context,
                                           keys=('pod_config',),
                                           default_value={})


def test_hardcoded_kubernetes_functions_not_used_during_ssh_provisioning():
    """Test that functions hardcoding cloud='kubernetes' would fail
    for SSH if called."""

    from unittest.mock import patch

    from sky.utils import config_utils

    # Setup: Config with DIFFERENT custom_metadata for SSH vs Kubernetes
    config_dict = {
        'ssh': {
            'custom_metadata': {
                'metadata': {
                    'labels': {
                        'source': 'ssh-config'
                    }
                }
            }
        },
        'kubernetes': {
            'custom_metadata': {
                'metadata': {
                    'labels': {
                        'source': 'k8s-config'
                    }
                }
            }
        }
    }

    # Simulate what would happen if merge_custom_metadata was called
    # during SSH provisioning with an SSH context
    ssh_context = 'ssh-my-cluster'

    with patch('sky.skypilot_config.get_effective_region_config') as mock_get:
        # Mock to return based on cloud parameter
        def get_config_side_effect(cloud,
                                   region,
                                   keys,
                                   default_value=None,
                                   override_configs=None):
            if cloud == 'kubernetes' and keys == ('custom_metadata',):
                return config_dict['kubernetes']['custom_metadata']
            elif cloud == 'ssh' and keys == ('custom_metadata',):
                return config_dict['ssh']['custom_metadata']
            return default_value

        mock_get.side_effect = get_config_side_effect

        # Call merge_custom_metadata with SSH context
        original_metadata = {}

        # This currently calls get_effective_region_config with cloud='ssh'
        utils.merge_custom_metadata(original_metadata, context=ssh_context)

        # Verify it was called with 'ssh'
        calls = mock_get.call_args_list
        custom_metadata_calls = [
            c for c in calls if c[1].get('keys') == ('custom_metadata',)
        ]

        assert len(custom_metadata_calls) >= 1
        assert custom_metadata_calls[0][1]['cloud'] == 'ssh', \
            "custom_metadata should use SSH cloud"


def test_combine_pod_config_fields_and_metadata_uses_correct_cloud():
    """Test that combine_pod_config_fields_and_metadata uses correct cloud for both parts.

    This function calls:
    1. combine_pod_config_fields
    2. combine_metadata_fields

    This test verifies that both configs are handled properly by their respective clouds.
    """
    from unittest.mock import patch

    from sky import clouds

    cluster_yaml = {
        'provider': {
            'autoscaler_service_account': {
                'metadata': {}
            },
            'autoscaler_role': {
                'metadata': {}
            },
            'autoscaler_role_binding': {
                'metadata': {}
            },
            'services': []
        },
        'available_node_types': {
            'ray_head_default': {
                'node_config': {
                    'spec': {
                        'containers': [{
                            'name': 'ray'
                        }]
                    },
                    'metadata': {}
                }
            }
        }
    }

    ssh_cloud = clouds.SSH()
    ssh_context = 'ssh-test-cluster'

    with patch('sky.skypilot_config.get_effective_region_config'
              ) as mock_get_config:
        config_calls = []

        def track_calls(cloud,
                        region,
                        keys,
                        default_value=None,
                        override_configs=None):
            config_calls.append({'cloud': cloud, 'keys': keys})
            return default_value or {}

        mock_get_config.side_effect = track_calls

        # Call the combined function
        result = utils.combine_pod_config_fields_and_metadata(
            cluster_yaml, {}, cloud=ssh_cloud, context=ssh_context)

        # Check calls to get_effective_region_config
        pod_config_calls = [
            c for c in config_calls if c['keys'] == ('pod_config',)
        ]
        custom_metadata_calls = [
            c for c in config_calls if c['keys'] == ('custom_metadata',)
        ]

        # pod_config should use 'ssh'
        assert len(pod_config_calls) >= 1
        assert pod_config_calls[0]['cloud'] == 'ssh', \
            "pod_config should use SSH cloud"

        # custom_metadata should use 'ssh'
        assert len(custom_metadata_calls) >= 1
        assert custom_metadata_calls[0]['cloud'] == 'ssh', \
            "custom_metadata should use SSH cloud"


@pytest.mark.parametrize('unsorted_pod_names, expected_sorted_pod_names', [
    ([
        'test-cluster-worker10', 'test-cluster-worker2', 'test-cluster-head',
        'test-cluster-worker1', 'test-cluster-worker3'
    ], [
        'test-cluster-head', 'test-cluster-worker1', 'test-cluster-worker2',
        'test-cluster-worker3', 'test-cluster-worker10'
    ]),
    ([
        'test-cluster-worker1', 'test-cluster-worker20', 'test-cluster-head',
        'test-cluster-worker3', 'test-cluster-worker2'
    ], [
        'test-cluster-head', 'test-cluster-worker1', 'test-cluster-worker2',
        'test-cluster-worker3', 'test-cluster-worker20'
    ]),
    ([
        'test-cluster-worker1', 'test-cluster-worker2', 'test-cluster-head',
        'test-cluster-worker3', 'test-cluster-worker4'
    ], [
        'test-cluster-head', 'test-cluster-worker1', 'test-cluster-worker2',
        'test-cluster-worker3', 'test-cluster-worker4'
    ]),
    ([
        'test-cluster-head', 'test-cluster-worker1', 'test-cluster-worker2',
        'test-cluster-worker3', 'test-cluster-worker4'
    ], [
        'test-cluster-head', 'test-cluster-worker1', 'test-cluster-worker2',
        'test-cluster-worker3', 'test-cluster-worker4'
    ]),
    ([
        'my-worker-head', 'my-worker-worker1', 'my-worker-worker2',
        'my-worker-worker3', 'my-worker-worker4'
    ], [
        'my-worker-head', 'my-worker-worker1', 'my-worker-worker2',
        'my-worker-worker3', 'my-worker-worker4'
    ]),
    ([
        'my-worker-head', 'my-worker-worker1', 'extra-pod', 'my-worker-worker2',
        'my-worker-worker3', 'my-worker-worker4'
    ], [
        'my-worker-head', 'my-worker-worker1', 'my-worker-worker2',
        'my-worker-worker3', 'my-worker-worker4', 'extra-pod'
    ]),
])
def test_filter_pods_sorts_by_name(unsorted_pod_names,
                                   expected_sorted_pod_names):
    """Test that filter_pods returns pods sorted correctly"""
    mock_pod_list = mock.MagicMock()
    mock_pod_list.items = []
    for pod_name in unsorted_pod_names:
        mock_pod = mock.MagicMock()
        mock_pod.metadata.name = pod_name
        mock_pod.metadata.deletion_timestamp = None
        mock_pod_list.items.append(mock_pod)

    with patch('sky.provision.kubernetes.utils.kubernetes.core_api'
              ) as mock_core_api:
        mock_core_api.return_value.list_namespaced_pod.return_value = mock_pod_list

        result = utils.filter_pods(namespace='test-namespace',
                                   context='test-context',
                                   tag_filters={'test-label': 'test-value'})

        # Verify the pods are returned in sorted order
        pod_names = list(result.keys())
        assert pod_names == expected_sorted_pod_names


class TestCheckInstanceFits:
    """Tests for check_instance_fits function."""

    def _create_mock_node(self,
                          name: str,
                          cpu_capacity: str,
                          memory_capacity: str,
                          is_ready: bool = True,
                          labels: Optional[dict] = None,
                          gpu_allocatable: Optional[str] = None):
        """Helper to create mock Kubernetes node."""
        mock_node = mock.MagicMock()
        mock_node.metadata.name = name
        mock_node.metadata.labels = labels or {}
        mock_node.status.capacity = {
            'cpu': cpu_capacity,
            'memory': memory_capacity
        }
        mock_node.status.allocatable = {
            'cpu': cpu_capacity,
            'memory': memory_capacity
        }
        if gpu_allocatable is not None:
            mock_node.status.allocatable['nvidia.com/gpu'] = gpu_allocatable
        mock_node.is_ready.return_value = is_ready
        return mock_node

    def test_cpu_instance_fits_on_cluster(self):
        """Test CPU-only instance that fits on the cluster."""
        mock_node = self._create_mock_node(name='cpu-node-1',
                                           cpu_capacity='16',
                                           memory_capacity='64Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--16GB')
            assert fits is True
            assert reason is None

    def test_cpu_instance_does_not_fit_insufficient_cpu(self):
        """Test CPU-only instance that doesn't fit due to insufficient CPU."""
        mock_node = self._create_mock_node(name='small-node',
                                           cpu_capacity='2',
                                           memory_capacity='64Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--8GB')
            assert fits is False
            assert reason is not None
            assert 'CPU' in reason

    def test_cpu_instance_does_not_fit_insufficient_memory(self):
        """Test CPU-only instance that doesn't fit due to insufficient memory."""
        mock_node = self._create_mock_node(name='low-memory-node',
                                           cpu_capacity='16',
                                           memory_capacity='8Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--16GB')
            assert fits is False
            assert reason is not None
            assert 'memory' in reason.lower()

    def test_cpu_instance_no_ready_nodes(self):
        """Test CPU-only instance when no nodes are ready."""
        mock_node = self._create_mock_node(name='not-ready-node',
                                           cpu_capacity='16',
                                           memory_capacity='64Gi',
                                           is_ready=False)

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--16GB')
            assert fits is False
            assert reason is not None
            assert 'No ready nodes' in reason

    def test_gpu_instance_fits_on_cluster(self):
        """Test GPU instance that fits on the cluster."""
        mock_node = self._create_mock_node(
            name='gpu-node-1',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-accelerator': 'nvidia-tesla-v100',
            },
            gpu_allocatable='4')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-accelerator',
                                   ['nvidia-tesla-v100'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.get_node_accelerator_count',
                       return_value=4):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--V100:2')
            assert fits is True
            assert reason is None

    def test_gpu_instance_gpu_type_not_available(self):
        """Test GPU instance when GPU type is not available on cluster."""
        mock_node = self._create_mock_node(name='cpu-node',
                                           cpu_capacity='32',
                                           memory_capacity='128Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       side_effect=exceptions.ResourcesUnavailableError('A100 not found')):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--A100:1')
            assert fits is False
            assert reason is not None
            assert 'A100 not found' in reason

    def test_gpu_instance_no_ready_gpu_nodes(self):
        """Test GPU instance when no ready GPU nodes are available."""
        mock_node = self._create_mock_node(
            name='gpu-node-not-ready',
            cpu_capacity='32',
            memory_capacity='128Gi',
            is_ready=False,
            labels={
                'cloud.google.com/gke-accelerator': 'nvidia-tesla-v100',
            },
            gpu_allocatable='4')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-accelerator',
                                   ['nvidia-tesla-v100'], None, None)):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--V100:2')
            assert fits is False
            assert reason is not None
            assert 'No ready GPU nodes' in reason

    def test_gpu_instance_insufficient_gpu_count(self):
        """Test GPU instance when nodes don't have enough GPUs."""
        mock_node = self._create_mock_node(
            name='gpu-node-1',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-accelerator': 'nvidia-tesla-v100',
            },
            gpu_allocatable='2')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-accelerator',
                                   ['nvidia-tesla-v100'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.get_node_accelerator_count',
                       return_value=2):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--V100:4')
            assert fits is False
            assert reason is not None
            assert 'No GPU nodes found with' in reason

    def test_gpu_instance_insufficient_cpu_on_gpu_node(self):
        """Test GPU instance when GPU nodes don't have enough CPU."""
        mock_node = self._create_mock_node(
            name='gpu-node-low-cpu',
            cpu_capacity='4',  # Only 4 CPUs
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-accelerator': 'nvidia-tesla-v100',
            },
            gpu_allocatable='4')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-accelerator',
                                   ['nvidia-tesla-v100'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.get_node_accelerator_count',
                       return_value=4):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--V100:2')
            assert fits is False
            assert reason is not None
            assert 'CPUs' in reason

    def test_multiple_nodes_one_fits(self):
        """Test that instance fits when at least one node has sufficient resources."""
        small_node = self._create_mock_node(name='small-node',
                                            cpu_capacity='4',
                                            memory_capacity='16Gi')
        large_node = self._create_mock_node(name='large-node',
                                            cpu_capacity='32',
                                            memory_capacity='128Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[small_node, large_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '16CPU--64GB')
            assert fits is True
            assert reason is None

    def test_empty_cluster(self):
        """Test when cluster has no nodes."""
        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--16GB')
            assert fits is False
            assert reason is not None
            assert 'No ready nodes' in reason

    def test_exact_resources_not_sufficient(self):
        """Test that exact resource match is not considered sufficient.

        The function requires strictly greater resources to account for
        kube-system pods consuming resources.
        """
        # Node has exactly 4 CPUs and 16GB, requesting 4CPU--16GB should not fit
        mock_node = self._create_mock_node(name='exact-match-node',
                                           cpu_capacity='4',
                                           memory_capacity='16Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '4CPU--16GB')
            assert fits is False
            assert reason is not None
            assert 'Maximum resources found' in reason

    def test_tpu_instance_fits(self):
        """Test TPU instance that fits on the cluster."""
        mock_node = self._create_mock_node(
            name='tpu-node-1',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-accelerator-count': '8',
                'cloud.google.com/gke-tpu-topology': '2x4'
            })
        mock_node.status.allocatable['google.com/tpu'] = '8'

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-tpu-accelerator',
                                   ['tpu-v4-podslice'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
                       return_value=True), \
             mock.patch('sky.provision.kubernetes.utils.normalize_tpu_accelerator_name',
                       return_value=('tpu-v4-podslice', 8)), \
             mock.patch('sky.provision.kubernetes.utils.is_multi_host_tpu',
                       return_value=False):
            fits, reason = utils.check_instance_fits(
                'test-context', '8CPU--32GB--tpu-v4-podslice:8')
            assert fits is True
            assert reason is None

    def test_context_none(self):
        """Test with None context."""
        mock_node = self._create_mock_node(name='node-1',
                                           cpu_capacity='16',
                                           memory_capacity='64Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits(None, '4CPU--16GB')
            assert fits is True
            assert reason is None

    def test_millicore_cpu_capacity(self):
        """Test node with millicore CPU capacity."""
        mock_node = self._create_mock_node(
            name='millicore-node',
            cpu_capacity='4000m',  # 4 CPUs in millicore
            memory_capacity='64Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            # Requesting 2 CPUs should fit on a 4 CPU node
            fits, reason = utils.check_instance_fits('test-context',
                                                     '2CPU--16GB')
            assert fits is True
            assert reason is None

    def test_fractional_cpu_instance(self):
        """Test instance with fractional CPU request."""
        mock_node = self._create_mock_node(name='node-1',
                                           cpu_capacity='4',
                                           memory_capacity='16Gi')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                        return_value=[mock_node]):
            fits, reason = utils.check_instance_fits('test-context',
                                                     '0.5CPU--2GB')
            assert fits is True
            assert reason is None

    def test_tpu_multi_host_skipped(self):
        """Test that multi-host TPU nodes are skipped.

        When a TPU node is a multi-host configuration, it should be skipped
        during the check, and if no single-host TPU nodes match, the check fails.
        """
        # Multi-host TPU node that should be skipped
        mock_multi_host_node = self._create_mock_node(
            name='tpu-multi-host-node',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-accelerator-count': '8',
                'cloud.google.com/gke-tpu-topology': '4x4',
                'cloud.google.com/gke-tpu-node-pool-type': 'multi-host'
            })
        mock_multi_host_node.status.allocatable['google.com/tpu'] = '8'

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_multi_host_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-tpu-accelerator',
                                   ['tpu-v4-podslice'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
                       return_value=True), \
             mock.patch('sky.provision.kubernetes.utils.normalize_tpu_accelerator_name',
                       return_value=('tpu-v4-podslice', 8)), \
             mock.patch('sky.provision.kubernetes.utils.is_multi_host_tpu',
                       return_value=True):
            fits, reason = utils.check_instance_fits(
                'test-context', '8CPU--32GB--tpu-v4-podslice:8')
            # Should fail because multi-host TPU is skipped and no other TPU found
            assert fits is False
            assert reason is not None
            assert 'Requested TPU type was not found' in reason

    def test_tpu_chip_count_mismatch(self):
        """Test TPU instance with mismatched chip count.

        When TPU type matches but chip count doesn't match, the function
        returns a list of available TPU configurations.
        """
        # TPU node with 4 chips, but we request 8
        mock_tpu_node = self._create_mock_node(
            name='tpu-node-4chip',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-accelerator-count': '4',
                'cloud.google.com/gke-tpu-topology': '2x2'
            })
        mock_tpu_node.status.allocatable['google.com/tpu'] = '4'

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_tpu_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-tpu-accelerator',
                                   ['tpu-v4-podslice'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
                       return_value=True), \
             mock.patch('sky.provision.kubernetes.utils.normalize_tpu_accelerator_name',
                       return_value=('tpu-v4-podslice', 8)), \
             mock.patch('sky.provision.kubernetes.utils.is_multi_host_tpu',
                       return_value=False):
            fits, reason = utils.check_instance_fits(
                'test-context', '8CPU--32GB--tpu-v4-podslice:8')
            assert fits is False
            assert reason is not None
            # Should mention available TPU types
            assert 'tpu-v4-podslice:4' in reason
            assert 'Requested TPU type was not found' in reason

    def test_gpu_label_values_none(self):
        """Test when get_accelerator_label_key_values returns None for values.

        When gpu_label_values is None, it should be converted to empty list,
        resulting in no matching GPU nodes found.
        """
        mock_node = self._create_mock_node(
            name='gpu-node',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-accelerator': 'nvidia-tesla-v100',
            },
            gpu_allocatable='4')

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-accelerator',
                                   None, None, None)):  # None for gpu_label_values
            fits, reason = utils.check_instance_fits('test-context',
                                                     '8CPU--32GB--V100:2')
            assert fits is False
            assert reason is not None
            assert 'No ready GPU nodes found' in reason

    def test_tpu_fits_returns_with_reason(self):
        """Test TPU instance that fits.

        This tests the path where check_tpu_fits returns True with reason=None.
        """
        mock_tpu_node = self._create_mock_node(
            name='tpu-node-1',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-accelerator-count': '8',
                'cloud.google.com/gke-tpu-topology': '2x4'
            })
        mock_tpu_node.status.allocatable['google.com/tpu'] = '8'

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_tpu_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-tpu-accelerator',
                                   ['tpu-v4-podslice'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
                       return_value=True), \
             mock.patch('sky.provision.kubernetes.utils.normalize_tpu_accelerator_name',
                       return_value=('tpu-v4-podslice', 8)), \
             mock.patch('sky.provision.kubernetes.utils.is_multi_host_tpu',
                       return_value=False):
            fits, reason = utils.check_instance_fits(
                'test-context', '8CPU--32GB--tpu-v4-podslice:8')
            assert fits is True
            assert reason is None

    def test_tpu_does_not_fit(self):
        """Test TPU instance that doesn't fit.

        When check_tpu_fits returns (False, reason_string), it should return False and the reason.
        """
        # TPU node with different chip count
        mock_tpu_node = self._create_mock_node(
            name='tpu-node-4chip',
            cpu_capacity='32',
            memory_capacity='128Gi',
            labels={
                'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
                'cloud.google.com/gke-accelerator-count': '4',
                'cloud.google.com/gke-tpu-topology': '2x2'
            })
        mock_tpu_node.status.allocatable['google.com/tpu'] = '4'

        with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                       return_value=[mock_tpu_node]), \
             mock.patch('sky.provision.kubernetes.utils.get_accelerator_label_key_values',
                       return_value=('cloud.google.com/gke-tpu-accelerator',
                                   ['tpu-v4-podslice'], None, None)), \
             mock.patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
                       return_value=True), \
             mock.patch('sky.provision.kubernetes.utils.normalize_tpu_accelerator_name',
                       return_value=('tpu-v4-podslice', 8)), \
             mock.patch('sky.provision.kubernetes.utils.is_multi_host_tpu',
                       return_value=False):
            fits, reason = utils.check_instance_fits(
                'test-context', '8CPU--32GB--tpu-v4-podslice:8')
            assert fits is False
            assert reason is not None
            assert 'Requested TPU type was not found' in reason
