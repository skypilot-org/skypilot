"""Unit tests for Kubernetes volume provisioning."""
from typing import Any, Dict, List
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from sky import global_user_state
from sky import models
from sky.provision import constants
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import volume as k8s_volume
from sky.utils import volume as volume_lib


class MockPVC:
    """Mock PVC object."""

    def __init__(self,
                 name: str,
                 namespace: str,
                 storage_class: str = 'standard',
                 size: str = '10Gi',
                 access_modes: List[str] = None):
        self.metadata = Mock()
        self.metadata.name = name
        self.metadata.namespace = namespace
        self.metadata.labels = {'parent': 'skypilot', 'skypilot-name': name}

        self.spec = Mock()
        self.spec.storage_class_name = storage_class
        self.spec.access_modes = access_modes or ['ReadWriteOnce']
        self.spec.resources = Mock()
        self.spec.resources.requests = {'storage': size}

        self.status = Mock()
        self.status.capacity = {'storage': size}


class MockPod:
    """Mock Pod object."""

    def __init__(self,
                 name: str,
                 namespace: str,
                 pvc_names: List[str] = None,
                 cluster_name: str = None):
        self.metadata = Mock()
        self.metadata.name = name
        self.metadata.namespace = namespace
        self.metadata.labels = {}
        if cluster_name:
            self.metadata.labels[
                constants.TAG_SKYPILOT_CLUSTER_NAME] = cluster_name

        self.spec = Mock()
        if pvc_names:
            self.spec.volumes = []
            for pvc_name in pvc_names:
                volume = Mock()
                volume.persistent_volume_claim = Mock()
                volume.persistent_volume_claim.claim_name = pvc_name
                self.spec.volumes.append(volume)
        else:
            self.spec.volumes = None


class TestGetContextNamespace:
    """Tests for _get_context_namespace function."""

    def test_get_context_namespace_with_region_and_namespace(self):
        """Test when both region and namespace are specified."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-vol-pvc',
            size=None,
            config={'namespace': 'my-namespace'},
        )
        context, namespace = k8s_volume._get_context_namespace(config)
        assert context == 'my-context'
        assert namespace == 'my-namespace'

    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'get_current_kube_config_context_name')
    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'get_kube_config_context_namespace')
    def test_get_context_namespace_without_region(self, mock_get_namespace,
                                                  mock_get_context):
        """Test when region is not specified."""
        mock_get_context.return_value = 'default-context'
        mock_get_namespace.return_value = 'default-namespace'

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='test-vol-pvc',
            size=None,
            config={},
        )
        context, namespace = k8s_volume._get_context_namespace(config)

        assert context == 'default-context'
        assert namespace == 'default-namespace'
        assert config.region == 'default-context'
        assert config.config['namespace'] == 'default-namespace'
        mock_get_context.assert_called_once()
        mock_get_namespace.assert_called_once_with('default-context')

    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'get_kube_config_context_namespace')
    def test_get_context_namespace_without_namespace(self, mock_get_namespace):
        """Test when namespace is not specified."""
        mock_get_namespace.return_value = 'inferred-namespace'

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-vol-pvc',
            size=None,
            config={},
        )
        context, namespace = k8s_volume._get_context_namespace(config)

        assert context == 'my-context'
        assert namespace == 'inferred-namespace'
        assert config.config['namespace'] == 'inferred-namespace'
        mock_get_namespace.assert_called_once_with('my-context')


class TestCheckPVCUsageForPod:
    """Tests for check_pvc_usage_for_pod function."""

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_check_pvc_usage_no_volumes(self, mock_k8s):
        """Test when pod spec has no volumes."""
        pod_spec = {'spec': {}}
        # Should not raise any exception
        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_check_pvc_usage_no_pvc_volumes(self, mock_k8s):
        """Test when pod spec has volumes but no PVC volumes."""
        pod_spec = {
            'spec': {
                'volumes': [{
                    'name': 'config-vol',
                    'configMap': {
                        'name': 'my-config'
                    }
                }]
            }
        }
        # Should not raise any exception
        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_check_pvc_usage_rwo_mode_not_used(self, mock_k8s, mock_get_usedby):
        """Test PVC with ReadWriteOnce mode not currently used."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.return_value = mock_pvc
        mock_get_usedby.return_value = ([], [])  # Not used by any pods

        pod_spec = {
            'spec': {
                'volumes': [{
                    'persistentVolumeClaim': {
                        'claimName': 'my-pvc'
                    }
                }]
            }
        }

        # Should not raise any exception
        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)
        mock_get_usedby.assert_called_once_with('my-context', 'my-namespace',
                                                'my-pvc')

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_check_pvc_usage_rwo_mode_already_used(self, mock_k8s,
                                                   mock_get_usedby):
        """Test PVC with ReadWriteOnce mode already in use."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.return_value = mock_pvc
        mock_get_usedby.return_value = (['other-pod'], ['other-cluster']
                                       )  # Already used

        pod_spec = {
            'spec': {
                'volumes': [{
                    'persistentVolumeClaim': {
                        'claimName': 'my-pvc'
                    }
                }]
            }
        }

        # Should raise KubernetesError
        with pytest.raises(config_lib.KubernetesError) as exc_info:
            k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                               pod_spec)
        assert 'already in use' in str(exc_info.value)
        assert 'my-pvc' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_check_pvc_usage_rwx_mode_already_used(self, mock_k8s,
                                                   mock_get_usedby):
        """Test PVC with ReadWriteMany mode already in use (should allow)."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteMany'])
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.return_value = mock_pvc
        mock_get_usedby.return_value = (['other-pod'], ['other-cluster'])

        pod_spec = {
            'spec': {
                'volumes': [{
                    'persistentVolumeClaim': {
                        'claimName': 'my-pvc'
                    }
                }]
            }
        }

        # Should not raise any exception (ReadWriteMany allows multiple users)
        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)


class TestApplyVolume:
    """Tests for apply_volume function."""

    @patch('sky.provision.kubernetes.volume.create_persistent_volume_claim')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.provision.kubernetes.volume._get_pvc_spec')
    def test_apply_volume_success(self, mock_get_spec, mock_get_context,
                                  mock_k8s, mock_create_pvc):
        """Test successful volume creation."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_spec.return_value = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {
                'storageClassName': 'standard',
                'accessModes': ['ReadWriteOnce'],
                'resources': {
                    'requests': {
                        'storage': '10Gi'
                    }
                }
            }
        }

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'access_mode': 'ReadWriteOnce'},
        )

        result = k8s_volume.apply_volume(config)

        assert result == config
        mock_k8s.storage_api.return_value.read_storage_class.assert_called_once_with(
            name='standard', _request_timeout=mock_k8s.API_TIMEOUT)
        mock_create_pvc.assert_called_once()

    @patch('sky.provision.kubernetes.volume.create_persistent_volume_claim')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.provision.kubernetes.volume._get_pvc_spec')
    def test_apply_volume_storage_class_not_found(self, mock_get_spec,
                                                  mock_get_context, mock_k8s,
                                                  mock_create_pvc):
        """Test when storage class doesn't exist."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_spec.return_value = {
            'metadata': {
                'name': 'test-pvc'
            },
            'spec': {
                'storageClassName': 'nonexistent'
            }
        }

        # Create an actual exception instance
        api_exception = Exception("Storage class not found")
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.storage_api.return_value.read_storage_class.side_effect = api_exception

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'access_mode': 'ReadWriteOnce'},
        )

        with pytest.raises(config_lib.KubernetesError) as exc_info:
            k8s_volume.apply_volume(config)
        assert 'storage class' in str(exc_info.value).lower()


class TestDeleteVolume:
    """Tests for delete_volume function."""

    @patch(
        'sky.provision.kubernetes.volume.kubernetes_utils.delete_k8s_resource_with_retry'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_delete_volume_success(self, mock_get_context, mock_delete):
        """Test successful volume deletion."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        result = k8s_volume.delete_volume(config)

        assert result == config
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args
        assert call_args[1]['resource_type'] == 'pvc'
        assert call_args[1]['resource_name'] == 'test-pvc'


class TestGetVolumeUsedBy:
    """Tests for _get_volume_usedby and related functions."""

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_no_pods(self, mock_get_map, mock_k8s):
        """Test when volume is not used by any pods."""
        mock_get_map.return_value = {}
        mock_pods = Mock()
        mock_pods.items = []
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_single_pod(self, mock_get_map, mock_k8s):
        """Test when volume is used by a single pod."""
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        mock_pod = MockPod('my-pod', 'my-namespace', ['test-pvc'],
                           'cluster-on-cloud')
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == ['my-pod']
        assert usedby_clusters == ['my-cluster']

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_multiple_pods(self, mock_get_map, mock_k8s):
        """Test when volume is used by multiple pods."""
        mock_get_map.return_value = {
            'cluster-1-on-cloud': 'cluster-1',
            'cluster-2-on-cloud': 'cluster-2'
        }

        mock_pod1 = MockPod('pod-1', 'my-namespace', ['test-pvc'],
                            'cluster-1-on-cloud')
        mock_pod2 = MockPod('pod-2', 'my-namespace', ['test-pvc'],
                            'cluster-2-on-cloud')
        mock_pods = Mock()
        mock_pods.items = [mock_pod1, mock_pod2]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert len(usedby_pods) == 2
        assert 'pod-1' in usedby_pods
        assert 'pod-2' in usedby_pods
        assert len(usedby_clusters) == 2
        assert 'cluster-1' in usedby_clusters
        assert 'cluster-2' in usedby_clusters

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_non_skypilot_pod(self, mock_get_map, mock_k8s):
        """Test when volume is used by a non-SkyPilot pod."""
        mock_get_map.return_value = {}

        # Pod without cluster label
        mock_pod = MockPod('external-pod', 'my-namespace', ['test-pvc'])
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == ['external-pod']
        assert usedby_clusters == []  # No cluster mapping

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_pod_no_volumes(self, mock_get_map, mock_k8s):
        """Test when pod has no volumes spec (covers line 125)."""
        mock_get_map.return_value = {}

        # Pod with no volumes
        mock_pod = MockPod('pod-no-volumes', 'my-namespace')
        mock_pod.spec.volumes = None
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_volume_no_pvc(self, mock_get_map, mock_k8s):
        """Test when volume has no PVC (covers line 128)."""
        mock_get_map.return_value = {}

        # Pod with volume but no PVC
        mock_pod = Mock()
        mock_pod.metadata.name = 'pod-with-configmap'
        mock_pod.spec.volumes = [Mock()]
        mock_pod.spec.volumes[0].persistent_volume_claim = None
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_volume_usedby_wrapper(self, mock_get_context, mock_get_usedby):
        """Test the get_volume_usedby wrapper function."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_usedby.return_value = (['pod-1'], ['cluster-1'])

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        usedby_pods, usedby_clusters = k8s_volume.get_volume_usedby(config)

        assert usedby_pods == ['pod-1']
        assert usedby_clusters == ['cluster-1']
        mock_get_usedby.assert_called_once_with('my-context', 'my-namespace',
                                                'test-pvc')


class TestGetClusterNameMap:
    """Tests for _get_cluster_name_on_cloud_to_cluster_name_map function."""

    @patch('sky.provision.kubernetes.volume.global_user_state.get_clusters')
    def test_get_cluster_name_map_empty(self, mock_get_clusters):
        """Test when there are no clusters."""
        mock_get_clusters.return_value = []

        result = k8s_volume._get_cluster_name_on_cloud_to_cluster_name_map()

        assert result == {}

    @patch('sky.provision.kubernetes.volume.global_user_state.get_clusters')
    def test_get_cluster_name_map_single_cluster(self, mock_get_clusters):
        """Test with a single cluster."""
        mock_handle = Mock()
        mock_handle.cluster_name_on_cloud = 'cloud-name-1'

        mock_get_clusters.return_value = [{
            'name': 'my-cluster',
            'handle': mock_handle
        }]

        result = k8s_volume._get_cluster_name_on_cloud_to_cluster_name_map()

        assert result == {'cloud-name-1': 'my-cluster'}

    @patch('sky.provision.kubernetes.volume.global_user_state.get_clusters')
    def test_get_cluster_name_map_multiple_clusters(self, mock_get_clusters):
        """Test with multiple clusters."""
        mock_handle1 = Mock()
        mock_handle1.cluster_name_on_cloud = 'cloud-name-1'
        mock_handle2 = Mock()
        mock_handle2.cluster_name_on_cloud = 'cloud-name-2'

        mock_get_clusters.return_value = [{
            'name': 'cluster-1',
            'handle': mock_handle1
        }, {
            'name': 'cluster-2',
            'handle': mock_handle2
        }]

        result = k8s_volume._get_cluster_name_on_cloud_to_cluster_name_map()

        assert result == {
            'cloud-name-1': 'cluster-1',
            'cloud-name-2': 'cluster-2'
        }

    @patch('sky.provision.kubernetes.volume.global_user_state.get_clusters')
    def test_get_cluster_name_map_null_handle(self, mock_get_clusters):
        """Test with clusters that have null handles."""
        mock_handle = Mock()
        mock_handle.cluster_name_on_cloud = 'cloud-name-1'

        mock_get_clusters.return_value = [
            {
                'name': 'cluster-1',
                'handle': mock_handle
            },
            {
                'name': 'cluster-2',
                'handle': None
            },  # Null handle
        ]

        result = k8s_volume._get_cluster_name_on_cloud_to_cluster_name_map()

        # Only cluster-1 should be in the map
        assert result == {'cloud-name-1': 'cluster-1'}


class TestGetAllVolumesUsedBy:
    """Tests for get_all_volumes_usedby function."""

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_empty(self, mock_get_context, mock_get_map,
                                          mock_k8s):
        """Test with no volumes."""
        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [])

        assert used_by_pods == {}
        assert used_by_clusters == {}

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_single_volume(self, mock_get_context,
                                                  mock_get_map, mock_k8s):
        """Test with a single volume."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        mock_pod = MockPod('my-pod', 'my-namespace', ['test-pvc'],
                           'cluster-on-cloud')
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        assert 'my-context' in used_by_pods
        assert 'my-namespace' in used_by_pods['my-context']
        assert 'test-pvc' in used_by_pods['my-context']['my-namespace']
        assert used_by_pods['my-context']['my-namespace']['test-pvc'] == [
            'my-pod'
        ]

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_pod_no_volumes(self, mock_get_context,
                                                   mock_get_map, mock_k8s):
        """Test with pod that has no volumes (covers line 198)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod with no volumes
        mock_pod = MockPod('pod-no-vols', 'my-namespace')
        mock_pod.spec.volumes = None
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        # Should have empty dict for the volume
        assert 'test-pvc' not in used_by_pods.get('my-context',
                                                  {}).get('my-namespace', {})

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_volume_no_pvc(self, mock_get_context,
                                                  mock_get_map, mock_k8s):
        """Test with volume that has no PVC (covers line 201)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod with volume but no PVC
        mock_pod = Mock()
        mock_pod.metadata.name = 'pod-with-configmap'
        mock_pod.metadata.labels = {}
        mock_pod.spec.volumes = [Mock()]
        mock_pod.spec.volumes[0].persistent_volume_claim = None
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        # Should have empty dict for the volume
        assert 'test-pvc' not in used_by_pods.get('my-context',
                                                  {}).get('my-namespace', {})

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_pvc_not_in_list(self, mock_get_context,
                                                    mock_get_map, mock_k8s):
        """Test with PVC not in the requested list (covers line 204)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod using a different PVC
        mock_pod = MockPod('pod-1', 'my-namespace', ['other-pvc'],
                           'cluster-on-cloud')
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        # Should not include the other-pvc
        assert 'test-pvc' not in used_by_pods.get('my-context',
                                                  {}).get('my-namespace', {})
        assert 'other-pvc' not in used_by_pods.get('my-context',
                                                   {}).get('my-namespace', {})

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_no_cluster_label(self, mock_get_context,
                                                     mock_get_map, mock_k8s):
        """Test with pod without cluster label (covers line 212)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod without cluster label
        mock_pod = MockPod('pod-no-label', 'my-namespace', ['test-pvc'])
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        # Pod should be recorded, but no cluster
        assert used_by_pods['my-context']['my-namespace']['test-pvc'] == [
            'pod-no-label'
        ]
        assert 'test-pvc' not in used_by_clusters.get('my-context', {}).get(
            'my-namespace', {})

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_cluster_not_in_map(self, mock_get_context,
                                                       mock_get_map, mock_k8s):
        """Test with cluster not in map (covers line 215)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}  # Empty map

        # Pod with cluster label but not in map
        mock_pod = MockPod('pod-1', 'my-namespace', ['test-pvc'],
                           'unknown-cluster')
        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_k8s.core_api.return_value.list_namespaced_pod.return_value = mock_pods

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [config])

        # Pod should be recorded, but no cluster (cluster not in map)
        assert used_by_pods['my-context']['my-namespace']['test-pvc'] == [
            'pod-1'
        ]
        # No cluster should be added since it's not in the map
        assert len(
            used_by_clusters.get('my-context', {}).get('my-namespace', {})) == 0

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_list_pods_exception(self, mock_get_context,
                                                        mock_get_map, mock_k8s):
        """Test when list_namespaced_pod raises an exception."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Make list_namespaced_pod raise an exception
        api_exception = Exception("Failed to list pods")
        mock_k8s.core_api.return_value.list_namespaced_pod.side_effect = api_exception

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        used_by_pods, used_by_clusters, failed_volume_names = k8s_volume.get_all_volumes_usedby(
            [config])

        # The volume should be marked as failed
        assert 'test-vol' in failed_volume_names
        # The namespace should still be initialized in the dictionaries
        assert 'my-context' in used_by_pods
        assert 'my-namespace' in used_by_pods['my-context']
        assert 'my-context' in used_by_clusters
        assert 'my-namespace' in used_by_clusters['my-context']
        # But the volume should not be in the used_by dictionaries
        assert 'test-pvc' not in used_by_pods['my-context']['my-namespace']
        assert 'test-pvc' not in used_by_clusters['my-context']['my-namespace']

    @patch('sky.provision.kubernetes.volume.kubernetes')
    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_list_pods_exception_multiple_volumes(
            self, mock_get_context, mock_get_map, mock_k8s):
        """Test exception handling with multiple volumes in same namespace."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Make list_namespaced_pod raise an exception
        api_exception = Exception("Failed to list pods")
        mock_k8s.core_api.return_value.list_namespaced_pod.side_effect = api_exception

        config1 = models.VolumeConfig(
            _version=1,
            name='test-vol-1',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc-1',
            size=None,
        )
        config2 = models.VolumeConfig(
            _version=1,
            name='test-vol-2',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc-2',
            size=None,
        )

        used_by_pods, used_by_clusters, failed_volume_names = k8s_volume.get_all_volumes_usedby(
            [config1, config2])

        # Both volumes should be marked as failed
        assert 'test-vol-1' in failed_volume_names
        assert 'test-vol-2' in failed_volume_names
        assert len(failed_volume_names) == 2


class TestMapAllVolumesUsedBy:
    """Tests for map_all_volumes_usedby function."""

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_map_all_volumes_usedby_found(self, mock_get_context):
        """Test mapping when volume is found in the used_by dicts."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        used_by_pods = {
            'my-context': {
                'my-namespace': {
                    'test-pvc': ['pod-1', 'pod-2']
                }
            }
        }
        used_by_clusters = {
            'my-context': {
                'my-namespace': {
                    'test-pvc': ['cluster-1']
                }
            }
        }

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        pods, clusters = k8s_volume.map_all_volumes_usedby(
            used_by_pods, used_by_clusters, config)

        assert pods == ['pod-1', 'pod-2']
        assert clusters == ['cluster-1']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_map_all_volumes_usedby_not_found(self, mock_get_context):
        """Test mapping when volume is not found in the used_by dicts."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        used_by_pods = {}
        used_by_clusters = {}

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )

        pods, clusters = k8s_volume.map_all_volumes_usedby(
            used_by_pods, used_by_clusters, config)

        assert pods == []
        assert clusters == []


class TestPopulateConfigFromPVC:
    """Tests for _populate_config_from_pvc function."""

    def test_populate_config_from_pvc_none(self):
        """Test with None PVC object."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
        )
        k8s_volume._populate_config_from_pvc(config, None)
        # Should not raise any exception

    def test_populate_config_from_pvc_full(self):
        """Test populating all fields from PVC."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           storage_class='fast-ssd',
                           size='100Gi')

        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        assert config.config.get('storage_class_name') == 'fast-ssd'
        assert config.size == '100'  # Converted from '100Gi' to string

    def test_populate_config_from_pvc_preserve_existing(self):
        """Test that existing config values are not overwritten."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='50',
            config={'storage_class_name': 'custom-class'},
        )

        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           storage_class='standard',
                           size='100Gi')

        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        # Storage class should not be overwritten
        assert config.config.get('storage_class_name') == 'custom-class'
        # Size should be overwritten with warning
        assert config.size == '100'

    def test_populate_config_from_pvc_status_capacity(self):
        """Test using status.capacity for size."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        mock_pvc = MockPVC('test-pvc', 'my-namespace', size='50Gi')
        # Actual bound size is larger
        mock_pvc.status.capacity = {'storage': '100Gi'}

        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        # Should use status.capacity (actual bound size)
        assert config.size == '100'

    def test_populate_config_from_pvc_invalid_size_format(self):
        """Test handling invalid size format."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        mock_pvc = MockPVC('test-pvc', 'my-namespace', size='invalid')
        mock_pvc.status.capacity = {'storage': 'invalid'}

        # Should not raise, just log warning
        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        # Size should remain None
        assert config.size is None

    def test_populate_config_from_pvc_fallback_to_requests(self):
        """Test fallback to spec.resources.requests (covers lines 262-265)."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        mock_pvc = MockPVC('test-pvc', 'my-namespace', size='50Gi')
        # Set status.capacity to None to trigger fallback
        mock_pvc.status.capacity = None

        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        # Should use spec.resources.requests as fallback
        assert config.size == '50'

    def test_populate_config_from_pvc_no_capacity_no_requests(self):
        """Test when both capacity and requests are missing."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        # Set both to None
        mock_pvc.status.capacity = None
        mock_pvc.spec.resources = None

        k8s_volume._populate_config_from_pvc(config, mock_pvc)

        # Size should remain None
        assert config.size is None


class TestCreatePersistentVolumeClaim:
    """Tests for create_persistent_volume_claim function."""

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_already_exists(self, mock_k8s):
        """Test when PVC already exists."""
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.return_value = mock_pvc

        pvc_spec = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        k8s_volume.create_persistent_volume_claim('my-namespace', 'my-context',
                                                  pvc_spec, config)

        # Should not create, just populate from existing
        mock_k8s.core_api.return_value.create_namespaced_persistent_volume_claim.assert_not_called(
        )

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_not_exists_use_existing_true(self, mock_k8s,
                                                     mock_find_pvc):
        """Test when PVC doesn't exist but use_existing is True."""
        # Mock that PVC is not found by name or label
        mock_find_pvc.return_value = None

        pvc_spec = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={'use_existing': True},
        )

        with pytest.raises(ValueError) as exc_info:
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context', pvc_spec,
                                                      config)
        assert 'does not exist' in str(exc_info.value)
        assert 'use_existing' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_success(self, mock_k8s):
        """Test successful PVC creation."""
        # Create an actual exception instance with status attribute
        api_exception = Exception("PVC not found")
        api_exception.status = 404
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        mock_created_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_k8s.core_api.return_value.create_namespaced_persistent_volume_claim.return_value = mock_created_pvc

        pvc_spec = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {
                'accessModes': ['ReadWriteOnce'],
                'resources': {
                    'requests': {
                        'storage': '10Gi'
                    }
                }
            }
        }

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        k8s_volume.create_persistent_volume_claim('my-namespace', 'my-context',
                                                  pvc_spec, config)

        # Should create PVC
        mock_k8s.core_api.return_value.create_namespaced_persistent_volume_claim.assert_called_once_with(
            namespace='my-namespace',
            body=pvc_spec,
            _request_timeout=mock_k8s.API_TIMEOUT)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_api_error(self, mock_k8s):
        """Test when API returns a non-404 error."""
        # Create an actual exception instance
        api_exception = Exception("Server error")
        api_exception.status = 500  # Server error
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        pvc_spec = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        with pytest.raises(Exception):
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context', pvc_spec)


class TestGetPVCSpec:
    """Tests for _get_pvc_spec function."""

    def test_get_pvc_spec_basic(self):
        """Test basic PVC spec generation."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'access_mode': 'ReadWriteOnce'},
        )

        spec = k8s_volume._get_pvc_spec('my-namespace', config)

        assert spec['metadata']['name'] == 'test-pvc'
        assert spec['metadata']['namespace'] == 'my-namespace'
        assert spec['metadata']['labels']['parent'] == 'skypilot'
        assert spec['metadata']['labels']['skypilot-name'] == 'test-vol'
        assert spec['spec']['accessModes'] == ['ReadWriteOnce']
        assert spec['spec']['resources']['requests']['storage'] == '10Gi'

    def test_get_pvc_spec_with_storage_class(self):
        """Test PVC spec with storage class."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='50',
            config={
                'access_mode': 'ReadWriteMany',
                'storage_class_name': 'fast-ssd'
            },
        )

        spec = k8s_volume._get_pvc_spec('my-namespace', config)

        assert spec['spec']['storageClassName'] == 'fast-ssd'
        assert spec['spec']['accessModes'] == ['ReadWriteMany']
        assert spec['spec']['resources']['requests']['storage'] == '50Gi'

    def test_get_pvc_spec_without_size(self):
        """Test PVC spec without size (for use_existing)."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={'access_mode': 'ReadWriteOnce'},
        )

        spec = k8s_volume._get_pvc_spec('my-namespace', config)

        # resources should not be in spec when size is None
        assert 'resources' not in spec['spec']

    def test_get_pvc_spec_with_custom_labels(self):
        """Test PVC spec with custom labels."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'access_mode': 'ReadWriteOnce'},
            labels={
                'custom-label': 'custom-value',
                'environment': 'production'
            },
        )

        spec = k8s_volume._get_pvc_spec('my-namespace', config)

        assert spec['metadata']['labels']['custom-label'] == 'custom-value'
        assert spec['metadata']['labels']['environment'] == 'production'
        # Default labels should still be present
        assert spec['metadata']['labels']['parent'] == 'skypilot'

    def test_get_pvc_spec_access_mode_required(self):
        """Test that access_mode is required."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={},  # No access_mode
        )

        with pytest.raises(AssertionError):
            k8s_volume._get_pvc_spec('my-namespace', config)


class MockPV:
    """Mock PersistentVolume object."""

    def __init__(self,
                 name: str,
                 storage_class: str = 'standard',
                 access_modes: List[str] = None,
                 phase: str = 'Available'):
        self.metadata = Mock()
        self.metadata.name = name

        self.spec = Mock()
        self.spec.storage_class_name = storage_class
        self.spec.access_modes = access_modes or ['ReadWriteOnce']

        self.status = Mock()
        self.status.phase = phase


class TestRefreshVolumeConfig:
    """Tests for refresh_volume_config function."""

    @patch('sky.provision.kubernetes.volume.kubernetes.in_cluster_context_name')
    def test_refresh_volume_config_region_none(self, mock_in_cluster_context):
        """When region is None, it should be set from in-cluster context."""
        mock_in_cluster_context.return_value = 'in-cluster-context'

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        need_refresh, new_config = k8s_volume.refresh_volume_config(config)

        assert need_refresh is True
        assert new_config.region == 'in-cluster-context'
        # The original object should also be updated in place
        assert config.region == 'in-cluster-context'
        mock_in_cluster_context.assert_called_once()

    def test_refresh_volume_config_region_set(self):
        """When region is already set, it should be kept and no refresh needed."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='existing-context',
            zone=None,
            name_on_cloud='test-pvc',
            size=None,
            config={},
        )

        need_refresh, new_config = k8s_volume.refresh_volume_config(config)

        assert need_refresh is False
        assert new_config.region == 'existing-context'
        assert config.region == 'existing-context'


class TestGetAllVolumesErrors:
    """Tests for get_all_volumes_errors function."""

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    def test_pvc_bound_no_error(self, mock_core_api, mock_get_context):
        """Test that bound PVCs have no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a bound PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Bound'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    @patch('sky.adaptors.kubernetes.storage_api')
    def test_pvc_pending_generic_error(self, mock_storage_api, mock_core_api,
                                       mock_get_context):
        """Test that pending PVCs without access mode mismatch get generic error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # No available PVs
        mock_pv_list = Mock()
        mock_pv_list.items = []
        mock_core_api.return_value.list_persistent_volume.return_value = mock_pv_list

        # Mock storage class with Immediate binding mode
        mock_storage_class = Mock()
        mock_storage_class.volume_binding_mode = 'Immediate'
        mock_storage_api.return_value.read_storage_class.return_value = mock_storage_class

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        assert 'test-vol' in errors
        assert errors['test-vol'] is not None
        assert 'pending' in errors['test-vol'].lower()
        assert 'kubectl describe pvc' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    def test_pvc_pending_access_mode_mismatch(self, mock_core_api,
                                              mock_get_context):
        """Test detection of access mode mismatch."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC requesting ReadWriteOnce
        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # Available PV only supports ReadWriteMany
        mock_pv = MockPV('test-pv',
                         storage_class='standard',
                         access_modes=['ReadWriteMany'],
                         phase='Available')
        mock_pv_list = Mock()
        mock_pv_list.items = [mock_pv]
        mock_core_api.return_value.list_persistent_volume.return_value = mock_pv_list

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        assert 'test-vol' in errors
        assert 'access mode mismatch' in errors['test-vol'].lower()
        assert 'ReadWriteOnce' in errors['test-vol']
        assert 'ReadWriteMany' in errors['test-vol']
        assert 'kubectl describe pvc' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    @patch('sky.adaptors.kubernetes.storage_api')
    def test_pvc_pending_wait_for_first_consumer(self, mock_storage_api,
                                                 mock_core_api,
                                                 mock_get_context):
        """Test that pending PVCs with WaitForFirstConsumer get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # No available PVs
        mock_pv_list = Mock()
        mock_pv_list.items = []
        mock_core_api.return_value.list_persistent_volume.return_value = mock_pv_list

        # Mock storage class with WaitForFirstConsumer binding mode
        mock_storage_class = Mock()
        mock_storage_class.volume_binding_mode = 'WaitForFirstConsumer'
        mock_storage_api.return_value.read_storage_class.return_value = mock_storage_class

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        # Should be None for WaitForFirstConsumer
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    @patch('sky.adaptors.kubernetes.storage_api')
    def test_pvc_pending_storage_class_read_failure(self, mock_storage_api,
                                                    mock_core_api,
                                                    mock_get_context):
        """Test that pending PVCs with storage class read failure get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'existent'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # No available PVs
        mock_pv_list = Mock()
        mock_pv_list.items = []
        mock_core_api.return_value.list_persistent_volume.return_value = mock_pv_list

        # Mock storage class read failure
        mock_storage_api.return_value.read_storage_class.side_effect = Exception(
            'Storage class read failure')

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        # Should be None when storage class read fails
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    @patch('sky.adaptors.kubernetes.storage_api')
    def test_pvc_pending_storage_class_empty(self, mock_storage_api,
                                             mock_core_api, mock_get_context):
        """Test that pending PVCs with missing storage class get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = ''

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # No available PVs
        mock_pv_list = Mock()
        mock_pv_list.items = []
        mock_core_api.return_value.list_persistent_volume.return_value = mock_pv_list

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        # Should be None when storage class read fails
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    def test_pvc_lost_error(self, mock_core_api, mock_get_context):
        """Test that lost PVCs get appropriate error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a lost PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Lost'

        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        errors = k8s_volume.get_all_volumes_errors([config])

        assert 'test-vol' in errors
        assert 'Lost' in errors['test-vol']
        assert 'kubectl describe pvc' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.adaptors.kubernetes.core_api')
    def test_pvc_list_exception(self, mock_core_api, mock_get_context):
        """Test handling of exceptions when listing PVCs."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Make list_namespaced_persistent_volume_claim raise an exception
        mock_core_api.return_value.list_namespaced_persistent_volume_claim.side_effect = Exception(
            'API error')

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='10',
            config={'namespace': 'my-namespace'},
        )

        # Should not raise, just return empty dict
        errors = k8s_volume.get_all_volumes_errors([config])
        assert errors == {}


class TestFindPVCByNameOrLabel:
    """Tests for _find_pvc_by_name_or_label function."""

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_find_by_exact_name(self, mock_k8s):
        """Test finding PVC by exact name."""
        mock_pvc = MockPVC('myvolume', 'my-namespace')
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.return_value = mock_pvc

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is not None
        assert result.metadata.name == 'myvolume'
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.assert_called_once(
        )

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_find_by_label_when_name_not_found(self, mock_k8s):
        """Test finding PVC by label when exact name doesn't exist."""
        # Name lookup fails with 404
        api_exception = Exception("PVC not found")
        api_exception.status = 404
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        # Label lookup succeeds
        mock_pvc = MockPVC('myvolume-abc123', 'my-namespace')
        mock_pvc.metadata.labels = {
            'parent': 'skypilot',
            'skypilot-name': 'myvolume'
        }
        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc]
        mock_k8s.core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is not None
        assert result.metadata.name == 'myvolume-abc123'
        mock_k8s.core_api.return_value.list_namespaced_persistent_volume_claim.assert_called_once_with(
            namespace='my-namespace',
            label_selector='skypilot-name=myvolume',
            _request_timeout=mock_k8s.API_TIMEOUT)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_find_not_found_by_name_or_label(self, mock_k8s):
        """Test when PVC is not found by either name or label."""
        # Name lookup fails with 404
        api_exception = Exception("PVC not found")
        api_exception.status = 404
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        # Label lookup returns empty list
        mock_pvc_list = Mock()
        mock_pvc_list.items = []
        mock_k8s.core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is None

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_find_multiple_pvcs_by_label_raises_error(self, mock_k8s):
        """Test that when multiple PVCs match label, raises error."""

        # Custom exception class so except clause doesn't catch ValueError
        class MockApiException(Exception):
            pass

        # Name lookup fails with 404
        api_exception = MockApiException("PVC not found")
        api_exception.status = 404
        mock_k8s.api_exception.return_value = MockApiException
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        # Label lookup returns multiple PVCs
        mock_pvc1 = MockPVC('myvolume-abc123', 'my-namespace')
        mock_pvc1.metadata.labels = {
            'parent': 'skypilot',
            'skypilot-name': 'myvolume'
        }
        mock_pvc2 = MockPVC('myvolume-def456', 'my-namespace')
        mock_pvc2.metadata.labels = {
            'parent': 'skypilot',
            'skypilot-name': 'myvolume'
        }
        mock_pvc_list = Mock()
        mock_pvc_list.items = [mock_pvc1, mock_pvc2]
        mock_k8s.core_api.return_value.list_namespaced_persistent_volume_claim.return_value = mock_pvc_list

        # Should raise error for ambiguous PVCs
        with pytest.raises(ValueError) as exc_info:
            k8s_volume._find_pvc_by_name_or_label('my-context', 'my-namespace',
                                                  'myvolume')

        assert 'Multiple PVCs found' in str(exc_info.value)
        assert 'myvolume-abc123' in str(exc_info.value)
        assert 'myvolume-def456' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_find_non_404_error_raises(self, mock_k8s):
        """Test that non-404 errors are raised."""
        # Name lookup fails with 500
        api_exception = Exception("Server error")
        api_exception.status = 500
        mock_k8s.api_exception.return_value = Exception
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = api_exception

        with pytest.raises(Exception):
            k8s_volume._find_pvc_by_name_or_label('my-context', 'my-namespace',
                                                  'myvolume')

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_label_lookup_api_error_returns_none(self, mock_k8s):
        """Test that API error during label lookup is caught and returns None."""

        # Custom exception class so except clause doesn't catch other exceptions
        class MockApiException(Exception):
            pass

        # Name lookup fails with 404
        name_api_exception = MockApiException("PVC not found")
        name_api_exception.status = 404
        mock_k8s.api_exception.return_value = MockApiException
        mock_k8s.core_api.return_value.read_namespaced_persistent_volume_claim.side_effect = name_api_exception

        # Label lookup fails with API error (e.g., 500)
        label_api_exception = MockApiException("Server error")
        label_api_exception.status = 500
        mock_k8s.core_api.return_value.list_namespaced_persistent_volume_claim.side_effect = label_api_exception

        # Should return None (not raise) because the exception is caught
        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is None


class TestCreatePVCWithUseExisting:
    """Tests for create_persistent_volume_claim with use_existing and label lookup."""

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_use_existing_finds_by_label(self, mock_k8s, mock_find_pvc):
        """Test use_existing finds PVC by label and updates name_on_cloud."""
        # Mock finding PVC by label
        mock_pvc = MockPVC('myvolume-abc123', 'my-namespace', size='50Gi')
        mock_pvc.metadata.labels = {
            'parent': 'skypilot',
            'skypilot-name': 'myvolume'
        }
        mock_find_pvc.return_value = mock_pvc

        pvc_spec = {
            'metadata': {
                'name': 'myvolume',  # Initial name_on_cloud = name
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='myvolume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='myvolume',  # Initially same as name
            size=None,
            config={'use_existing': True},
        )

        k8s_volume.create_persistent_volume_claim('my-namespace', 'my-context',
                                                  pvc_spec, config)

        # name_on_cloud should be updated to actual PVC name
        assert config.name_on_cloud == 'myvolume-abc123'
        # Should not try to create PVC
        mock_k8s.core_api.return_value.create_namespaced_persistent_volume_claim.assert_not_called(
        )
        # Should call find with volume name
        mock_find_pvc.assert_called_once_with('my-context', 'my-namespace',
                                              'myvolume')

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_use_existing_not_found_raises_error(self, mock_k8s, mock_find_pvc):
        """Test use_existing raises error when PVC not found."""
        mock_find_pvc.return_value = None

        pvc_spec = {
            'metadata': {
                'name': 'myvolume',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='myvolume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='myvolume',
            size=None,
            config={'use_existing': True},
        )

        with pytest.raises(ValueError) as exc_info:
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context', pvc_spec,
                                                      config)

        assert 'does not exist' in str(exc_info.value)
        assert 'use_existing' in str(exc_info.value)
        assert 'skypilot-name=myvolume' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_use_existing_populates_config(self, mock_k8s, mock_find_pvc):
        """Test use_existing populates config from found PVC."""
        mock_pvc = MockPVC('myvolume-abc123',
                           'my-namespace',
                           storage_class='fast-ssd',
                           size='100Gi')
        mock_find_pvc.return_value = mock_pvc

        pvc_spec = {
            'metadata': {
                'name': 'myvolume',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='myvolume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='myvolume',
            size=None,
            config={'use_existing': True},
        )

        k8s_volume.create_persistent_volume_claim('my-namespace', 'my-context',
                                                  pvc_spec, config)

        # Config should be populated from PVC
        assert config.name_on_cloud == 'myvolume-abc123'
        assert config.size == '100'  # Converted from '100Gi'
        assert config.config.get('storage_class_name') == 'fast-ssd'

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_use_existing_api_error_raises_value_error(self, mock_k8s,
                                                       mock_find_pvc):
        """Test that API error from _find_pvc_by_name_or_label raises ValueError."""

        # Custom exception class to simulate kubernetes.api_exception()
        class MockApiException(Exception):
            pass

        mock_k8s.api_exception.return_value = MockApiException

        # Make _find_pvc_by_name_or_label raise an API exception
        api_error = MockApiException("Connection refused")
        mock_find_pvc.side_effect = api_error

        pvc_spec = {
            'metadata': {
                'name': 'myvolume',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        config = models.VolumeConfig(
            _version=1,
            name='myvolume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='myvolume',
            size=None,
            config={'use_existing': True},
        )

        with pytest.raises(ValueError) as exc_info:
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context', pvc_spec,
                                                      config)

        assert 'Failed to search for PVC' in str(exc_info.value)
        assert 'skypilot-name=myvolume' in str(exc_info.value)
