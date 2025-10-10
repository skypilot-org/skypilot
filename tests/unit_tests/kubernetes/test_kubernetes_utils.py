"""Tests for Kubernetes utils.

"""

import os
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
                   'get_all_pods_in_kubernetes_cluster',
                   return_value=[mock_pod_1, mock_pod_2]), \
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
                   'get_all_pods_in_kubernetes_cluster',
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
                   'get_all_pods_in_kubernetes_cluster',
                   return_value=[mock_pod_1]):
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
                   'get_all_pods_in_kubernetes_cluster',
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
                   'get_all_pods_in_kubernetes_cluster') as mock_get_pods:
        node_info = utils.get_kubernetes_node_info()

        mock_get_pods.assert_not_called()
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
                   'get_all_pods_in_kubernetes_cluster',
                   return_value=[mock_pod_1]) as mock_get_pods, \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key',
                   return_value='nvidia.com/gpu'):
        node_info = utils.get_kubernetes_node_info()

        mock_get_pods.assert_called_once()
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


# pylint: disable=line-too-long
def test_heterogenous_gpu_detection_key_counts():
    """Tests that a heterogenous gpu cluster with empty
    labels are correctly processed."""

    mock_node1 = mock.MagicMock()
    mock_node1.metadata.name = 'node1'
    mock_node1.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-h100-80gb',
        'gpu.nvidia.com/class': 'nvidia-h100-80gb',
        'gpu.nvidia.com/count': '1',
        'gpu.nvidia.com/model': 'nvidia-h100-80gb',
        'gpu.nvidia.com/vram': '81'
    }
    mock_node1.status.allocatable = {'nvidia.com/gpu': '1'}

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
         mock.patch('sky.provision.kubernetes.utils.get_all_pods_in_kubernetes_cluster', return_value=[mock_pod1, mock_pod2]), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key', return_value='nvidia.com/gpu'):

        counts, capacity, available = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)
        assert (set(counts.keys()) == set(capacity.keys()) == set(available.keys())), \
            (f'Keys of counts ({list(counts.keys())}), capacity ({list(capacity.keys())}), '
             f'and available ({list(available.keys())}) must be the same.')


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
                   'get_all_pods_in_kubernetes_cluster',
                   return_value=[mock_regular_pod, mock_low_priority_pod]), \
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


def test_accelerator_scaling_transient_state():
    """Tests that accelerator discovery handles nodes during scaling (with labels but zero capacity)."""
    # Mock node that is scaling up - has labels but zero allocatable GPUs
    mock_scaling_node = mock.MagicMock()
    mock_scaling_node.metadata.name = 'scaling-node'
    mock_scaling_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-l4',
        'cloud.google.com/gke-accelerator-count': '2'
    }
    # Node is not ready yet, so allocatable shows 0
    mock_scaling_node.status.allocatable = {'nvidia.com/gpu': '0'}
    mock_scaling_node.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.1')
    ]

    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name', return_value='test-context'), \
         mock.patch('sky.provision.kubernetes.utils.check_credentials', return_value=[True]), \
         mock.patch('sky.provision.kubernetes.utils.detect_accelerator_resource', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.detect_gpu_label_formatter', return_value=[utils.GKELabelFormatter(), None]), \
         mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes', return_value=[mock_scaling_node]), \
         mock.patch('sky.provision.kubernetes.utils.get_all_pods_in_kubernetes_cluster', return_value=[]), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key', return_value='nvidia.com/gpu'):

        counts, capacity, available = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)

        # All three dicts should have the same keys, even though capacity is 0
        assert (set(counts.keys()) == set(capacity.keys()) == set(available.keys())), \
            (f'Keys of counts ({list(counts.keys())}), capacity ({list(capacity.keys())}), '
             f'and available ({list(available.keys())}) must be the same.')

        # L4 should be in all dicts
        assert 'L4' in counts
        assert 'L4' in capacity
        assert 'L4' in available

        # Capacity and available should be 0 for the scaling node
        assert capacity['L4'] == 0
        assert available['L4'] == 0


def test_accelerator_mixed_ready_and_scaling_nodes():
    """Tests handling of clusters with both ready and scaling nodes."""
    # Mock ready node with available GPUs
    mock_ready_node = mock.MagicMock()
    mock_ready_node.metadata.name = 'ready-node'
    mock_ready_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-l4',
        'cloud.google.com/gke-accelerator-count': '4'
    }
    mock_ready_node.status.allocatable = {'nvidia.com/gpu': '4'}
    mock_ready_node.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.1')
    ]

    # Mock scaling node with labels but zero allocatable
    mock_scaling_node = mock.MagicMock()
    mock_scaling_node.metadata.name = 'scaling-node'
    mock_scaling_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-l4',
        'cloud.google.com/gke-accelerator-count': '4'
    }
    mock_scaling_node.status.allocatable = {'nvidia.com/gpu': '0'}
    mock_scaling_node.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.2')
    ]

    # Mock pod consuming 2 GPUs on ready node
    mock_pod = mock.MagicMock()
    mock_pod.spec.node_name = 'ready-node'
    mock_pod.status.phase = 'Running'
    mock_pod.metadata.name = 'gpu-pod'
    mock_pod.metadata.namespace = 'default'
    mock_container = mock.MagicMock()
    mock_container.resources.requests = {'nvidia.com/gpu': '2'}
    mock_pod.spec.containers = [mock_container]

    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name', return_value='test-context'), \
         mock.patch('sky.provision.kubernetes.utils.check_credentials', return_value=[True]), \
         mock.patch('sky.provision.kubernetes.utils.detect_accelerator_resource', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.detect_gpu_label_formatter', return_value=[utils.GKELabelFormatter(), None]), \
         mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes', return_value=[mock_ready_node, mock_scaling_node]), \
         mock.patch('sky.provision.kubernetes.utils.get_all_pods_in_kubernetes_cluster', return_value=[mock_pod]), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key', return_value='nvidia.com/gpu'):

        counts, capacity, available = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)

        # Keys should match
        assert (set(counts.keys()) == set(capacity.keys()) == set(available.keys())), \
            (f'Keys of counts ({list(counts.keys())}), capacity ({list(capacity.keys())}), '
             f'and available ({list(available.keys())}) must be the same.')

        # Should have L4 with capacity of 4 (only from ready node)
        assert 'L4' in capacity
        assert capacity['L4'] == 4

        # Available should be 2 (4 total - 2 used on ready node, scaling node contributes 0)
        assert available['L4'] == 2


def test_accelerator_no_allocatable_but_labels():
    """Tests nodes with accelerator labels but completely unschedulable (no allocatable reported)."""
    # Mock node with labels but no allocatable resources at all (e.g., node initializing)
    mock_node = mock.MagicMock()
    mock_node.metadata.name = 'initializing-node'
    mock_node.metadata.labels = {
        'cloud.google.com/gke-accelerator': 'nvidia-a100-80gb',
        'cloud.google.com/gke-accelerator-count': '8'
    }
    mock_node.status.allocatable = {}  # Empty allocatable
    mock_node.status.addresses = [
        mock.MagicMock(type='InternalIP', address='10.0.0.1')
    ]

    with mock.patch('sky.clouds.cloud_in_iterable', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.get_current_kube_config_context_name', return_value='test-context'), \
         mock.patch('sky.provision.kubernetes.utils.check_credentials', return_value=[True]), \
         mock.patch('sky.provision.kubernetes.utils.detect_accelerator_resource', return_value=True), \
         mock.patch('sky.provision.kubernetes.utils.detect_gpu_label_formatter', return_value=[utils.GKELabelFormatter(), None]), \
         mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes', return_value=[mock_node]), \
         mock.patch('sky.provision.kubernetes.utils.get_all_pods_in_kubernetes_cluster', return_value=[]), \
         mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key', return_value='nvidia.com/gpu'):

        counts, capacity, available = kubernetes_catalog.list_accelerators_realtime(
            True, None, None, None)

        # Keys should still match (even if all are empty or have the accelerator with 0 capacity)
        assert (set(counts.keys()) == set(capacity.keys()) == set(available.keys())), \
            (f'Keys of counts ({list(counts.keys())}), capacity ({list(capacity.keys())}), '
             f'and available ({list(available.keys())}) must be the same.')

        # Since allocatable is 0, A100-80GB should have 0 capacity
        if 'A100-80GB' in capacity:
            assert capacity['A100-80GB'] == 0
            assert available['A100-80GB'] == 0
