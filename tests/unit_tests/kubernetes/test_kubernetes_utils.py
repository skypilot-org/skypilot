"""Tests for Kubernetes utils.

"""

import os
import tempfile
from unittest import mock
from unittest.mock import patch

import kubernetes
import pytest

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
    mock_node_1 = mock.MagicMock()
    mock_node_1.metadata.name = 'node-1'
    mock_node_1.metadata.labels = {
        'skypilot.co/accelerator': 'a100-80gb',
        'cloud.google.com/gke-accelerator-count': '4'
    }
    mock_node_1.status.allocatable = {'nvidia.com/gpu': '4'}

    mock_node_2 = mock.MagicMock()
    mock_node_2.metadata.name = 'node-2'
    mock_node_2.metadata.labels = {
        'skypilot.co/accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-accelerator-count': '8',
        'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-tpu-topology': '2x4'
    }
    mock_node_2.status.allocatable = {'google.com/tpu': '8'}

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
                   return_value=[mock_node_1, mock_node_2]), \
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
                   return_value=[mock_node_1, mock_node_2]), \
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
    mock_node_3 = mock.MagicMock()
    mock_node_3.metadata.name = 'node-3'
    mock_node_3.metadata.labels = {
        'skypilot.co/accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-accelerator-count': '4',
        'cloud.google.com/gke-tpu-accelerator': 'tpu-v4-podslice',
        'cloud.google.com/gke-tpu-topology': '4x4',
        'cloud.google.com/gke-tpu-node-pool-type': 'multi-host'
    }
    mock_node_3.status.allocatable = {'google.com/tpu': '4'}

    with mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[mock_node_1, mock_node_3]), \
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
