"""Unit tests for Kubernetes volume provisioning."""
from typing import Any, Dict, List
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky import global_user_state
from sky import models
from sky.adaptors import kubernetes
from sky.provision import constants
from sky.provision.kubernetes import config as config_lib
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import reads
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
        # A claim that is not being resized reports neither, and the real
        # client gives None rather than a Mock -- which a bare Mock() would
        # make truthy and iterable-looking.
        self.status.allocated_resource_statuses = None
        self.status.allocated_resources = None
        self.status.conditions = None


class MockPod:
    """Mock Pod object."""

    def __init__(self,
                 name: str,
                 namespace: str,
                 pvc_names: List[str] = None,
                 cluster_name: str = None,
                 terminating: bool = False):
        self.metadata = Mock()
        self.metadata.name = name
        self.metadata.namespace = namespace
        self.metadata.deletion_timestamp = (Mock() if terminating else None)
        self.metadata.labels = {}
        if cluster_name:
            self.metadata.labels[
                constants.TAG_SKYPILOT_CLUSTER_NAME] = cluster_name

        self.status = Mock()

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


class FakeReads:
    """Answers reads.list_objects() and reads.get_object() from memory.

    `lists` maps a resource to the objects a list returns, to a function of
    the namespace that returns them, or to an exception to raise. `gets` maps
    a (resource, name) pair, or a resource for any name, to the object a get
    returns or to an exception to raise; any other get finds no object.
    """

    def __init__(self) -> None:
        self.lists: Dict[Any, Any] = {}
        self.gets: Dict[Any, Any] = {}
        self.list_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []

    def list_objects(self, context, resource, **kwargs):
        self.list_calls.append(dict(kwargs, context=context, resource=resource))
        got = self.lists.get(resource, [])
        if isinstance(got, BaseException):
            raise got
        if callable(got):
            got = got(kwargs.get('namespace'))
        return _read_result(got)

    def get_object(self, context, resource, name, **kwargs):
        self.get_calls.append(
            dict(kwargs, context=context, resource=resource, name=name))
        got = self.gets.get((resource, name), self.gets.get(resource))
        if isinstance(got, BaseException):
            raise got
        return _read_result([] if got is None else [got])


def _read_result(items) -> reads.ReadResult:
    return reads.ReadResult(items=list(items),
                            resource_version='',
                            staleness_seconds=0,
                            reason=None)


def _read_error(status=None) -> exceptions.KubeAPIUnreachableError:
    """A failed read; `status` is what the API server answered, if anything."""
    error = exceptions.KubeAPIUnreachableError(f'Kubernetes API error {status}')
    if status is not None:
        error.__cause__ = kubernetes.api_exception()(status=status,
                                                     reason='Error')
    return error


@pytest.fixture(name='fake_reads')
def _fake_reads(monkeypatch):
    fake = FakeReads()
    monkeypatch.setattr(reads, 'list_objects', fake.list_objects)
    monkeypatch.setattr(reads, 'get_object', fake.get_object)
    return fake


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

    @patch('sky.clouds.Kubernetes.existing_allowed_contexts')
    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'get_current_kube_config_context_name')
    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'get_kube_config_context_namespace')
    def test_get_context_namespace_without_region(self, mock_get_namespace,
                                                  mock_get_context,
                                                  mock_allowed_contexts):
        """Test when region is not specified."""
        mock_get_context.return_value = 'default-context'
        mock_get_namespace.return_value = 'default-namespace'
        # The current context is allowed, so it is the one that gets used.
        mock_allowed_contexts.return_value = ['default-context']

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

    def test_check_pvc_usage_no_volumes(self):
        """Test when pod spec has no volumes."""
        pod_spec = {'spec': {}}
        # Should not raise any exception
        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)

    def test_check_pvc_usage_no_pvc_volumes(self):
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
    def test_check_pvc_usage_rwo_mode_not_used(self, mock_get_usedby,
                                               fake_reads):
        """Test PVC with ReadWriteOnce mode not currently used."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = mock_pvc
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
    def test_check_pvc_usage_rwo_mode_already_used(self, mock_get_usedby,
                                                   fake_reads):
        """Test PVC with ReadWriteOnce mode already in use."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = mock_pvc
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
    def test_check_pvc_usage_rwx_mode_already_used(self, mock_get_usedby,
                                                   fake_reads):
        """Test PVC with ReadWriteMany mode already in use (should allow)."""
        mock_pvc = MockPVC('my-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteMany'])
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = mock_pvc
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

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    def test_check_pvc_usage_reads_the_pvc_fresh(self, mock_get_usedby,
                                                 fake_reads):
        """The check gates pod creation, so it reads the API server."""
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS,
                        'my-pvc'] = MockPVC('my-pvc',
                                            'my-namespace',
                                            access_modes=['ReadWriteOnce'])
        mock_get_usedby.return_value = ([], [])
        pod_spec = {
            'spec': {
                'volumes': [{
                    'persistentVolumeClaim': {
                        'claimName': 'my-pvc'
                    }
                }]
            }
        }

        k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                           pod_spec)

        [call] = fake_reads.get_calls
        assert call['context'] == 'my-context'
        assert call['namespace'] == 'my-namespace'
        assert call['consistency'] == reads.FRESH

    @patch('sky.provision.kubernetes.volume._get_volume_usedby')
    def test_check_pvc_usage_missing_pvc_raises(self, mock_get_usedby,
                                                fake_reads):
        """A pod cannot mount a PVC that does not exist."""
        del fake_reads  # Knows no PVC.
        pod_spec = {
            'spec': {
                'volumes': [{
                    'persistentVolumeClaim': {
                        'claimName': 'my-pvc'
                    }
                }]
            }
        }

        with pytest.raises(config_lib.KubernetesError, match='my-pvc'):
            k8s_volume.check_pvc_usage_for_pod('my-context', 'my-namespace',
                                               pod_spec)
        mock_get_usedby.assert_not_called()


class TestApplyVolume:
    """Tests for apply_volume function."""

    @patch('sky.provision.kubernetes.volume.create_persistent_volume_claim')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.provision.kubernetes.volume._get_pvc_spec')
    def test_apply_volume_success(self, mock_get_spec, mock_get_context,
                                  mock_create_pvc, fake_reads):
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

        fake_reads.gets[reads.STORAGE_CLASSES, 'standard'] = {
            'metadata': {
                'name': 'standard'
            }
        }

        result = k8s_volume.apply_volume(config)

        assert result == config
        assert [(c['resource'], c['name'], c['consistency'])
                for c in fake_reads.get_calls] == [(reads.STORAGE_CLASSES,
                                                    'standard', reads.FRESH)]
        mock_create_pvc.assert_called_once()

    @patch('sky.provision.kubernetes.volume.create_persistent_volume_claim')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @patch('sky.provision.kubernetes.volume._get_pvc_spec')
    def test_apply_volume_storage_class_not_found(self, mock_get_spec,
                                                  mock_get_context,
                                                  mock_create_pvc, fake_reads):
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

        del fake_reads  # Knows no storage class.

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
        assert 'not found' in str(exc_info.value)
        mock_create_pvc.assert_not_called()


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


class TestWaitForDaemonsetReady:
    """Tests for _wait_for_daemonset_ready."""

    @staticmethod
    def _daemonset(desired, ready):
        return {
            'status': {
                'desiredNumberScheduled': desired,
                'numberReady': ready
            }
        }

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(k8s_volume.time_module, 'sleep', lambda _: None)

    def test_returns_once_every_pod_is_ready(self, fake_reads):
        answers = iter([
            _read_error(500), None,
            self._daemonset(2, 1),
            self._daemonset(2, 2)
        ])

        def _get(context, resource, name, **kwargs):
            fake_reads.get_calls.append(
                dict(kwargs, context=context, resource=resource, name=name))
            answer = next(answers)
            if isinstance(answer, BaseException):
                raise answer
            return _read_result([] if answer is None else [answer])

        with patch.object(reads, 'get_object', _get):
            k8s_volume._wait_for_daemonset_ready('my-context', 'my-namespace',
                                                 'cleanup-ds')

        # An API error and a DaemonSet not yet visible both keep it polling.
        assert len(fake_reads.get_calls) == 4
        for call in fake_reads.get_calls:
            assert call['resource'] == reads.DAEMON_SETS
            assert call['name'] == 'cleanup-ds'
            assert call['namespace'] == 'my-namespace'
            assert call['consistency'] == reads.FRESH
            assert call['attempts'] == 1

    def test_unreachable_api_server_raises(self, fake_reads):
        error = _read_error(None)
        fake_reads.gets[reads.DAEMON_SETS] = error

        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc_info:
            k8s_volume._wait_for_daemonset_ready('my-context', 'my-namespace',
                                                 'cleanup-ds')
        assert exc_info.value is error


class TestGetVolumeUsedBy:
    """Tests for _get_volume_usedby and related functions."""

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_no_pods(self, mock_get_map, fake_reads):
        """Test when volume is not used by any pods."""
        mock_get_map.return_value = {}
        fake_reads.lists[reads.PODS] = []

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []
        # It gates pod creation and volume deletion, so it reads the API
        # server, and only pods that may still hold the claim.
        [call] = fake_reads.list_calls
        assert call['resource'] == reads.PODS
        assert call['namespace'] == 'my-namespace'
        assert call['consistency'] == reads.FRESH
        assert call['field_selector'] == ','.join(
            f'status.phase!={phase}'
            for phase in k8s_constants.PVC_NOT_HOLD_POD_PHASES)

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_list_error_raises(self, mock_get_map,
                                                 fake_reads):
        """A failed pod list must not read as an unused volume."""
        mock_get_map.return_value = {}
        error = _read_error(500)
        fake_reads.lists[reads.PODS] = error

        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc_info:
            k8s_volume._get_volume_usedby('my-context', 'my-namespace',
                                          'test-pvc')
        assert exc_info.value is error

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_single_pod(self, mock_get_map, fake_reads):
        """Test when volume is used by a single pod."""
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        mock_pod = MockPod('my-pod', 'my-namespace', ['test-pvc'],
                           'cluster-on-cloud')
        fake_reads.lists[reads.PODS] = [mock_pod]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == ['my-pod']
        assert usedby_clusters == ['my-cluster']

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_multiple_pods(self, mock_get_map, fake_reads):
        """Test when volume is used by multiple pods."""
        mock_get_map.return_value = {
            'cluster-1-on-cloud': 'cluster-1',
            'cluster-2-on-cloud': 'cluster-2'
        }

        mock_pod1 = MockPod('pod-1', 'my-namespace', ['test-pvc'],
                            'cluster-1-on-cloud')
        mock_pod2 = MockPod('pod-2', 'my-namespace', ['test-pvc'],
                            'cluster-2-on-cloud')
        fake_reads.lists[reads.PODS] = [mock_pod1, mock_pod2]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert len(usedby_pods) == 2
        assert 'pod-1' in usedby_pods
        assert 'pod-2' in usedby_pods
        assert len(usedby_clusters) == 2
        assert 'cluster-1' in usedby_clusters
        assert 'cluster-2' in usedby_clusters

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_non_skypilot_pod(self, mock_get_map, fake_reads):
        """Test when volume is used by a non-SkyPilot pod."""
        mock_get_map.return_value = {}

        # Pod without cluster label
        mock_pod = MockPod('external-pod', 'my-namespace', ['test-pvc'])
        fake_reads.lists[reads.PODS] = [mock_pod]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == ['external-pod']
        assert usedby_clusters == []  # No cluster mapping

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_pod_no_volumes(self, mock_get_map, fake_reads):
        """Test when pod has no volumes spec (covers line 125)."""
        mock_get_map.return_value = {}

        # Pod with no volumes
        mock_pod = MockPod('pod-no-volumes', 'my-namespace')
        mock_pod.spec.volumes = None
        fake_reads.lists[reads.PODS] = [mock_pod]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_volume_no_pvc(self, mock_get_map, fake_reads):
        """Test when volume has no PVC (covers line 128)."""
        mock_get_map.return_value = {}

        # Pod with volume but no PVC
        mock_pod = Mock()
        mock_pod.metadata.name = 'pod-with-configmap'
        mock_pod.metadata.deletion_timestamp = None
        mock_pod.spec.volumes = [Mock()]
        mock_pod.spec.volumes[0].persistent_volume_claim = None
        fake_reads.lists[reads.PODS] = [mock_pod]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == []
        assert usedby_clusters == []

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_skips_terminating_pod(self, mock_get_map,
                                                     fake_reads):
        """Test that terminating pods are skipped."""
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        # One running pod and one terminating pod, both using the same PVC
        running_pod = MockPod('running-pod', 'my-namespace', ['test-pvc'],
                              'cluster-on-cloud')
        terminating_pod = MockPod('terminating-pod',
                                  'my-namespace', ['test-pvc'],
                                  'cluster-on-cloud',
                                  terminating=True)
        fake_reads.lists[reads.PODS] = [running_pod, terminating_pod]

        usedby_pods, usedby_clusters = k8s_volume._get_volume_usedby(
            'my-context', 'my-namespace', 'test-pvc')

        assert usedby_pods == ['running-pod']
        assert usedby_clusters == ['my-cluster']

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    def test_get_volume_usedby_all_pods_terminating(self, mock_get_map,
                                                    fake_reads):
        """Test that volume appears unused when all pods are terminating."""
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        terminating_pod = MockPod('terminating-pod',
                                  'my-namespace', ['test-pvc'],
                                  'cluster-on-cloud',
                                  terminating=True)
        fake_reads.lists[reads.PODS] = [terminating_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_empty(self, mock_get_context, mock_get_map):
        """Test with no volumes."""
        used_by_pods, used_by_clusters, _ = k8s_volume.get_all_volumes_usedby(
            [])

        assert used_by_pods == {}
        assert used_by_clusters == {}

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_single_volume(self, mock_get_context,
                                                  mock_get_map, fake_reads):
        """Test with a single volume."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {'cluster-on-cloud': 'my-cluster'}

        mock_pod = MockPod('my-pod', 'my-namespace', ['test-pvc'],
                           'cluster-on-cloud')
        fake_reads.lists[reads.PODS] = [mock_pod]

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
        [call] = fake_reads.list_calls
        assert call['label_selector'] == 'parent=skypilot'
        assert call['consistency'] == reads.CACHED

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_skips_terminating_pod(
            self, mock_get_context, mock_get_map, fake_reads):
        """Test that terminating pods are skipped in get_all_volumes_usedby."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {
            'cluster-1-on-cloud': 'cluster-1',
            'cluster-2-on-cloud': 'cluster-2'
        }

        running_pod = MockPod('running-pod', 'my-namespace', ['test-pvc'],
                              'cluster-1-on-cloud')
        terminating_pod = MockPod('terminating-pod',
                                  'my-namespace', ['test-pvc'],
                                  'cluster-2-on-cloud',
                                  terminating=True)
        fake_reads.lists[reads.PODS] = [running_pod, terminating_pod]

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

        pods_list = used_by_pods['my-context']['my-namespace'].get(
            'test-pvc', [])
        assert pods_list == ['running-pod']
        # used_by_clusters is keyed by cluster_name
        assert 'cluster-1' in used_by_clusters['my-context']['my-namespace'][
            'test-pvc']
        assert 'cluster-2' not in used_by_clusters['my-context'][
            'my-namespace']['test-pvc']

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_pod_no_volumes(self, mock_get_context,
                                                   mock_get_map, fake_reads):
        """Test with pod that has no volumes (covers line 198)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod with no volumes
        mock_pod = MockPod('pod-no-vols', 'my-namespace')
        mock_pod.spec.volumes = None
        fake_reads.lists[reads.PODS] = [mock_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_volume_no_pvc(self, mock_get_context,
                                                  mock_get_map, fake_reads):
        """Test with volume that has no PVC (covers line 201)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod with volume but no PVC
        mock_pod = Mock()
        mock_pod.metadata.name = 'pod-with-configmap'
        mock_pod.metadata.deletion_timestamp = None
        mock_pod.metadata.labels = {}
        mock_pod.spec.volumes = [Mock()]
        mock_pod.spec.volumes[0].persistent_volume_claim = None
        fake_reads.lists[reads.PODS] = [mock_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_pvc_not_in_list(self, mock_get_context,
                                                    mock_get_map, fake_reads):
        """Test with PVC not in the requested list (covers line 204)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod using a different PVC
        mock_pod = MockPod('pod-1', 'my-namespace', ['other-pvc'],
                           'cluster-on-cloud')
        fake_reads.lists[reads.PODS] = [mock_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_no_cluster_label(self, mock_get_context,
                                                     mock_get_map, fake_reads):
        """Test with pod without cluster label (covers line 212)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # Pod without cluster label
        mock_pod = MockPod('pod-no-label', 'my-namespace', ['test-pvc'])
        fake_reads.lists[reads.PODS] = [mock_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_cluster_not_in_map(self, mock_get_context,
                                                       mock_get_map,
                                                       fake_reads):
        """Test with cluster not in map (covers line 215)."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}  # Empty map

        # Pod with cluster label but not in map
        mock_pod = MockPod('pod-1', 'my-namespace', ['test-pvc'],
                           'unknown-cluster')
        fake_reads.lists[reads.PODS] = [mock_pod]

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_list_pods_exception(self, mock_get_context,
                                                        mock_get_map,
                                                        fake_reads):
        """Test when the pod list fails."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # The pod list fails
        fake_reads.lists[reads.PODS] = _read_error(500)

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

    @patch(
        'sky.provision.kubernetes.volume._get_cluster_name_on_cloud_to_cluster_name_map'
    )
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_get_all_volumes_usedby_list_pods_exception_multiple_volumes(
            self, mock_get_context, mock_get_map, fake_reads):
        """Test exception handling with multiple volumes in same namespace."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        mock_get_map.return_value = {}

        # The pod list fails
        fake_reads.lists[reads.PODS] = _read_error(500)

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
        assert config.config.get('access_mode') == 'ReadWriteOnce'

    def test_populate_access_mode_inferred(self):
        """Test access_mode is inferred from PVC when not set in config."""
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
                           access_modes=['ReadWriteMany'])
        k8s_volume._populate_config_from_pvc(config, mock_pvc)
        assert config.config.get('access_mode') == 'ReadWriteMany'

    def test_populate_access_mode_override_with_warning(self):
        """Test access_mode is overridden when PVC differs from config."""
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
        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteMany'])
        k8s_volume._populate_config_from_pvc(config, mock_pvc)
        assert config.config.get('access_mode') == 'ReadWriteMany'

    def test_populate_access_mode_matching(self):
        """Test no change when PVC access_mode matches config."""
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
        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        k8s_volume._populate_config_from_pvc(config, mock_pvc)
        assert config.config.get('access_mode') == 'ReadWriteOnce'

    def test_populate_access_mode_empty(self):
        """Test no change when PVC has no access_modes."""
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
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.spec.access_modes = None
        k8s_volume._populate_config_from_pvc(config, mock_pvc)
        assert config.config.get('access_mode') == 'ReadWriteOnce'

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
    def test_create_pvc_already_exists(self, mock_k8s, fake_reads):
        """Test when PVC already exists."""
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = mock_pvc

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
    def test_create_pvc_not_exists_use_existing_true(self, mock_find_pvc):
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
        # The PVC may well exist -- just on a context this lookup never
        # searched, e.g. when the volume named none and one was resolved
        # for it. Say which cluster came up empty, and how to pin another.
        assert 'my-context' in str(exc_info.value)
        assert 'my-namespace' in str(exc_info.value)
        assert '--infra k8s/<context>' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_success(self, mock_k8s, fake_reads):
        """Test successful PVC creation."""
        # No PVC is named 'test-pvc', so it is created.
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
        assert [(c['resource'], c['name'], c['consistency'])
                for c in fake_reads.get_calls
               ] == [(reads.PERSISTENT_VOLUME_CLAIMS, 'test-pvc', reads.FRESH)]

    @patch('sky.provision.kubernetes.volume.kubernetes')
    def test_create_pvc_api_error(self, mock_k8s, fake_reads):
        """Test when API returns a non-404 error."""
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = _read_error(500)

        pvc_spec = {
            'metadata': {
                'name': 'test-pvc',
                'namespace': 'my-namespace'
            },
            'spec': {}
        }

        with pytest.raises(exceptions.KubeAPIUnreachableError):
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context', pvc_spec)
        mock_k8s.core_api.return_value.create_namespaced_persistent_volume_claim.assert_not_called(
        )


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

    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'is_incluster_config_available')
    @patch('sky.provision.kubernetes.volume.kubernetes.in_cluster_context_name')
    def test_refresh_volume_config_region_none_incluster(
            self, mock_in_cluster_context, mock_is_incluster):
        """region=None + in-cluster auth available: rewrite to in-cluster name."""
        mock_in_cluster_context.return_value = 'in-cluster-context'
        mock_is_incluster.return_value = True

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

    @patch('sky.provision.kubernetes.volume.kubernetes_utils.'
           'is_incluster_config_available')
    @patch('sky.provision.kubernetes.volume.kubernetes.in_cluster_context_name')
    def test_refresh_volume_config_region_none_kubeconfig_only(
            self, mock_in_cluster_context, mock_is_incluster):
        """region=None + no in-cluster auth: must NOT rewrite to 'in-cluster'.

        Regression test for the race surfaced by build #10973
        (test_hostpath_volume_on_kubernetes). Mechanism:

        1. ``sky volumes apply`` (no ``infra:`` field) inserts a row with
           region=None.
        2. The 60s volume-refresh daemon ticks (sky/server/daemons.py:182,
           VOLUME_REFRESH_DAEMON_INTERVAL_SECONDS=60).
        3. The unfixed ``refresh_volume_config`` unconditionally rewrites
           region=None to ``kubernetes.in_cluster_context_name()``, which
           returns the literal string ``'in-cluster'`` when
           SKYPILOT_IN_CLUSTER_CONTEXT_NAME is unset (the default).
        4. On the next ``sky launch``, Task topology forces
           ``task.resources.region = volume.region`` (sky/task.py:1122-1134),
           and the optimizer's ``_check_specified_regions`` rejects
           ``'in-cluster'`` because it isn't in the kubeconfig contexts
           (sky/optimizer.py:1647-1668) -> raises
           ``ResourcesUnavailableError: ... requires volume X with infra
           Kubernetes/in-cluster which is not enabled``.

        Pass-then-fail-then-pass-on-retry pattern in the wild is purely the
        60s daemon-tick race: if the tick fires in the window between
        ``apply`` and ``launch``, the row is poisoned. The fix is to NOT
        rewrite when in-cluster auth isn't actually available — leaving
        region=None lets the launch resolve the context from ``--infra``.
        """
        mock_is_incluster.return_value = False

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

        # If these assertions ever fail, the production bug is back:
        # any sky launch referencing this volume on a kubeconfig-only host
        # will raise ResourcesUnavailableError after the next daemon tick.
        assert need_refresh is False, (
            'refresh_volume_config rewrote region=None to '
            f'{new_config.region!r} even though in-cluster auth is '
            'unavailable. Production effect: every subsequent sky launch '
            'referencing this volume on a kubeconfig-only host '
            '(e.g. kind buildkite agent) will raise '
            'ResourcesUnavailableError: "requires volume X with infra '
            'Kubernetes/in-cluster which is not enabled" once the 60s '
            'volume-refresh daemon ticks.')
        assert new_config.region is None, (
            f'Expected region to stay None; got {new_config.region!r}.')
        assert config.region is None, (
            f'Expected in-place region to stay None; got {config.region!r}.')
        mock_in_cluster_context.assert_not_called()

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


class TestGetAllVolumesState:
    """Tests for get_all_volumes_state function."""

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_bound_no_error(self, mock_get_context, fake_reads):
        """Test that bound PVCs have no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a bound PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Bound'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

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

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_generic_error(self, mock_get_context, fake_reads):
        """Test that pending PVCs without access mode mismatch get generic error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        # No available PVs
        fake_reads.lists[reads.PERSISTENT_VOLUMES] = []

        # Mock storage class with Immediate binding mode
        fake_reads.gets[reads.STORAGE_CLASSES] = {
            'volumeBindingMode': 'Immediate'
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
            config={'namespace': 'my-namespace'},
        )

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert 'test-vol' in errors
        assert errors['test-vol'] is not None
        assert 'pending' in errors['test-vol'].lower()
        assert 'kubectl describe pvc' in errors['test-vol']
        # The message has to carry the shared sentence, not just say something
        # similar: it is what tells a launch that this volume is on its way
        # rather than broken, so a launch is not refused over it. If the wording
        # drifts from the constant, the launch silently starts refusing again.
        assert volume_lib.PVC_PROVISIONING_MESSAGE in errors['test-vol']
        assert volume_lib.volume_error_may_resolve(errors['test-vol'])

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_access_mode_mismatch(self, mock_get_context,
                                              fake_reads):
        """Test detection of access mode mismatch."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC requesting ReadWriteOnce
        mock_pvc = MockPVC('test-pvc',
                           'my-namespace',
                           access_modes=['ReadWriteOnce'])
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        # Available PV only supports ReadWriteMany
        mock_pv = MockPV('test-pv',
                         storage_class='standard',
                         access_modes=['ReadWriteMany'],
                         phase='Available')
        fake_reads.lists[reads.PERSISTENT_VOLUMES] = [mock_pv]

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

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert 'test-vol' in errors
        assert 'access mode mismatch' in errors['test-vol'].lower()
        assert 'ReadWriteOnce' in errors['test-vol']
        assert 'ReadWriteMany' in errors['test-vol']
        assert 'kubectl describe pvc' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_wait_for_first_consumer(self, mock_get_context,
                                                 fake_reads):
        """Test that pending PVCs with WaitForFirstConsumer get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        # No available PVs
        fake_reads.lists[reads.PERSISTENT_VOLUMES] = []

        # Mock storage class with WaitForFirstConsumer binding mode
        fake_reads.gets[reads.STORAGE_CLASSES] = {
            'volumeBindingMode': 'WaitForFirstConsumer'
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
            config={'namespace': 'my-namespace'},
        )

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        # Should be None for WaitForFirstConsumer
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_storage_class_read_failure(self, mock_get_context,
                                                    fake_reads):
        """Test that pending PVCs with storage class read failure get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'existent'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        # No available PVs
        fake_reads.lists[reads.PERSISTENT_VOLUMES] = []

        # Mock storage class read failure
        fake_reads.gets[reads.STORAGE_CLASSES] = _read_error(500)

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

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        # Should be None when storage class read fails
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_storage_class_empty(self, mock_get_context,
                                             fake_reads):
        """Test that pending PVCs with missing storage class get no error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a pending PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = ''

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        # No available PVs
        fake_reads.lists[reads.PERSISTENT_VOLUMES] = []

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

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        # Should be None when storage class read fails
        assert errors.get('test-vol') is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_lost_error(self, mock_get_context, fake_reads):
        """Test that lost PVCs get appropriate error."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # Create a lost PVC
        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Lost'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

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

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert 'test-vol' in errors
        assert 'Lost' in errors['test-vol']
        assert 'kubectl describe pvc' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_list_exception(self, mock_get_context, fake_reads):
        """Test handling of exceptions when listing PVCs."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        # The PVC list fails
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = _read_error(500)

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

        # Should not raise. The volume must be reported as failed rather than
        # simply omitted -- an omitted volume reads as healthy to the caller.
        errors, _, failed = k8s_volume.get_all_volumes_state([config])
        assert errors == {}
        assert failed == {'test-vol'}

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_direct_pvc_read_error_marks_volume_failed(self, mock_get_context,
                                                       fake_reads):
        """A PVC read that failed tells nothing about whether it exists."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = []
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = _read_error(500)
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

        errors, observed, failed = k8s_volume.get_all_volumes_state([config])

        assert errors == {}
        assert observed == {}
        assert failed == {'test-vol'}

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_volume_state_accepts_cached_reads(self, mock_get_context,
                                               fake_reads):
        """Volume state is a view, so a cache may serve its reads."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace')
        pvc.status.phase = 'Pending'
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [pvc]
        fake_reads.gets[reads.STORAGE_CLASSES, 'standard'] = {
            'volumeBindingMode': 'WaitForFirstConsumer'
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
            config={'namespace': 'my-namespace'},
        )

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert errors == {'test-vol': None}
        reads_made = {(c['resource'], c.get('consistency', reads.CACHED))
                      for c in fake_reads.list_calls + fake_reads.get_calls}
        assert (reads.PERSISTENT_VOLUME_CLAIMS, reads.CACHED) in reads_made
        assert (reads.PERSISTENT_VOLUMES, reads.CACHED) in reads_made
        assert (reads.STORAGE_CLASSES, reads.CACHED) in reads_made
        assert all(consistency == reads.CACHED for _, consistency in reads_made)

    def test_hostpath_volume_reported_clean(self):
        """hostPath volumes have no PVC, but they still have to appear in the
        result -- a caller cannot tell "omitted" from "unknown"."""
        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-hostpath',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-vol',
            size='10',
            config={'host_path': '/mnt/data'},
        )

        errors, _, failed = k8s_volume.get_all_volumes_state([config])

        assert errors == {'test-vol': None}
        assert failed == set()

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_deleted_outside_skypilot_reports_error(self, mock_get_context,
                                                        fake_reads):
        """A registered volume whose PVC is gone must not read as healthy."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = []

        # The direct read finds no 'test-pvc' either.
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

        errors, _, failed = k8s_volume.get_all_volumes_state([config])

        assert 'test-vol' not in failed
        assert errors['test-vol'] is not None
        assert 'no longer exists' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_unlabelled_pvc_is_read_directly(self, mock_get_context,
                                             fake_reads):
        """use_existing PVCs lack the skypilot label, so the listing misses
        them; a direct read must still find and evaluate them."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = []

        adopted_pvc = MockPVC('adopted-pvc', 'my-namespace')
        adopted_pvc.status.phase = 'Bound'
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = adopted_pvc

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='adopted-pvc',
            size='10',
            config={
                'namespace': 'my-namespace',
                'use_existing': True
            },
        )

        errors, _, failed = k8s_volume.get_all_volumes_state([config])

        assert failed == set()
        assert errors['test-vol'] is None

    @patch('sky.provision.kubernetes.volume.kubernetes_utils.get_pvc_events')
    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_pvc_pending_immediate_surfaces_csi_message(self, mock_get_context,
                                                        mock_get_events,
                                                        fake_reads):
        """The provisioner's rejection reason is what identifies the fault,
        so it has to reach the error message verbatim."""
        mock_get_context.return_value = ('my-context', 'my-namespace')

        mock_pvc = MockPVC('test-pvc', 'my-namespace')
        mock_pvc.status.phase = 'Pending'
        mock_pvc.spec.storage_class_name = 'standard'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        fake_reads.lists[reads.PERSISTENT_VOLUMES] = []

        fake_reads.gets[reads.STORAGE_CLASSES] = {
            'volumeBindingMode': 'Immediate'
        }

        event = Mock()
        event.type = 'Warning'
        event.reason = 'ProvisioningFailed'
        event.message = 'requested size is below the 1Ti minimum'
        mock_get_events.return_value = [event]

        config = models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='1000',
            config={'namespace': 'my-namespace'},
        )

        errors, _, _ = k8s_volume.get_all_volumes_state([config])

        assert 'ProvisioningFailed' in errors['test-vol']
        assert 'below the 1Ti minimum' in errors['test-vol']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_same_pvc_name_in_two_namespaces_is_not_confused(
            self, mock_get_context, fake_reads):
        """Claims are resolved per (context, namespace): the same claim name in
        another namespace belongs to a different volume, and reading one's phase
        as the other's would report the wrong volume broken."""
        by_volume = {'vol-a': ('ctx', 'ns-a'), 'vol-b': ('ctx', 'ns-b')}
        mock_get_context.side_effect = lambda cfg: by_volume[cfg.name]

        bound = MockPVC('shared-name', 'ns-a')
        bound.status.phase = 'Bound'
        lost = MockPVC('shared-name', 'ns-b')
        lost.status.phase = 'Lost'

        def _list(namespace):
            return [bound] if namespace == 'ns-a' else [lost]

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = _list

        def _config(name):
            return models.VolumeConfig(
                _version=1,
                name=name,
                type='k8s-pvc',
                cloud='kubernetes',
                region='ctx',
                zone=None,
                name_on_cloud='shared-name',
                size='10',
                config={},
            )

        errors, _, failed = k8s_volume.get_all_volumes_state(
            [_config('vol-a'), _config('vol-b')])

        assert failed == set()
        assert errors['vol-a'] is None
        assert errors['vol-b'] is not None
        assert 'Lost' in errors['vol-b']

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_one_broken_volume_among_several_is_reported(
            self, mock_get_context, fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')

        good = MockPVC('good-pvc', 'my-namespace')
        good.status.phase = 'Bound'
        bad = MockPVC('bad-pvc', 'my-namespace')
        bad.status.phase = 'Lost'

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [good, bad]

        def _config(name, pvc):
            return models.VolumeConfig(
                _version=1,
                name=name,
                type='k8s-pvc',
                cloud='kubernetes',
                region='my-context',
                zone=None,
                name_on_cloud=pvc,
                size='10',
                config={'namespace': 'my-namespace'},
            )

        errors, _, _ = k8s_volume.get_all_volumes_state(
            [_config('good-vol', 'good-pvc'),
             _config('bad-vol', 'bad-pvc')])

        assert errors['good-vol'] is None
        assert errors['bad-vol'] is not None


class TestFindPVCByNameOrLabel:
    """Tests for _find_pvc_by_name_or_label function."""

    def test_find_by_exact_name(self, fake_reads):
        """Test finding PVC by exact name."""
        mock_pvc = MockPVC('myvolume', 'my-namespace')
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS, 'myvolume'] = mock_pvc

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is not None
        assert result.metadata.name == 'myvolume'
        assert len(fake_reads.get_calls) == 1
        assert fake_reads.get_calls[0]['consistency'] == reads.FRESH
        assert not fake_reads.list_calls

    def test_find_by_label_when_name_not_found(self, fake_reads):
        """Test finding PVC by label when exact name doesn't exist."""
        # No PVC is named 'myvolume'; one carries it as its label.
        mock_pvc = MockPVC('myvolume-abc123', 'my-namespace')
        mock_pvc.metadata.labels = {
            'parent': 'skypilot',
            'skypilot-name': 'myvolume'
        }
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [mock_pvc]

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is not None
        assert result.metadata.name == 'myvolume-abc123'
        assert len(fake_reads.list_calls) == 1
        call = fake_reads.list_calls[0]
        assert call['namespace'] == 'my-namespace'
        assert call['label_selector'] == 'skypilot-name=myvolume'
        assert call['consistency'] == reads.FRESH

    def test_find_not_found_by_name_or_label(self, fake_reads):
        """Test when PVC is not found by either name or label."""
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = []

        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is None

    def test_find_multiple_pvcs_by_label_raises_error(self, fake_reads):
        """Test that when multiple PVCs match label, raises error."""
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
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [
            mock_pvc1, mock_pvc2
        ]

        # Should raise error for ambiguous PVCs
        with pytest.raises(ValueError) as exc_info:
            k8s_volume._find_pvc_by_name_or_label('my-context', 'my-namespace',
                                                  'myvolume')

        assert 'Multiple PVCs found' in str(exc_info.value)
        assert 'myvolume-abc123' in str(exc_info.value)
        assert 'myvolume-def456' in str(exc_info.value)

    def test_find_non_404_error_raises(self, fake_reads):
        """Test that non-404 errors are raised."""
        error = _read_error(500)
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = error

        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc_info:
            k8s_volume._find_pvc_by_name_or_label('my-context', 'my-namespace',
                                                  'myvolume')
        assert exc_info.value is error
        assert not fake_reads.list_calls

    def test_label_lookup_api_error_returns_none(self, fake_reads):
        """Test that API error during label lookup is caught and returns None."""
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = _read_error(500)

        # Should return None (not raise) because the exception is caught
        result = k8s_volume._find_pvc_by_name_or_label('my-context',
                                                       'my-namespace',
                                                       'myvolume')

        assert result is None

    def test_label_lookup_unreachable_raises(self, fake_reads):
        """A label lookup that got no answer is not a PVC that is absent."""
        error = _read_error(None)
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = error

        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc_info:
            k8s_volume._find_pvc_by_name_or_label('my-context', 'my-namespace',
                                                  'myvolume')
        assert exc_info.value is error


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
    def test_use_existing_not_found_raises_error(self, mock_find_pvc):
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
        # Sizes are read with Kubernetes' quantity grammar, so the mocked
        # adaptor has to keep that one real for the size to be read at all.
        mock_k8s.parse_quantity = kubernetes.parse_quantity
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
    def test_use_existing_api_error_raises_value_error(self, mock_find_pvc):
        """Test that API error from _find_pvc_by_name_or_label raises ValueError."""
        mock_find_pvc.side_effect = _read_error(403)

        config = self._use_existing_config()

        with pytest.raises(ValueError) as exc_info:
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context',
                                                      self._PVC_SPEC, config)

        assert 'Failed to search for PVC' in str(exc_info.value)
        assert 'skypilot-name=myvolume' in str(exc_info.value)

    @patch('sky.provision.kubernetes.volume._find_pvc_by_name_or_label')
    def test_use_existing_unreachable_propagates(self, mock_find_pvc):
        """A search that got no answer from the API server is not reworded."""
        error = _read_error(None)
        mock_find_pvc.side_effect = error

        config = self._use_existing_config()

        with pytest.raises(exceptions.KubeAPIUnreachableError) as exc_info:
            k8s_volume.create_persistent_volume_claim('my-namespace',
                                                      'my-context',
                                                      self._PVC_SPEC, config)
        assert exc_info.value is error

    _PVC_SPEC = {
        'metadata': {
            'name': 'myvolume',
            'namespace': 'my-namespace'
        },
        'spec': {}
    }

    @staticmethod
    def _use_existing_config() -> models.VolumeConfig:
        return models.VolumeConfig(
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


# Verbatim messages from a GKE Filestore cluster: the first from a claim whose
# storage class named a tier that does not exist, the next two from a claim that
# emitted them for minutes and then bound.
_TERMINAL_MESSAGE = (
    'Volume provisioning failed with infeasible error. Retries will be '
    'delayed. rpc error: code = InvalidArgument desc = Invalid value at '
    "'instance.tier' \"not-a-real-tier\", invalid")
_DEADLINE_MESSAGE = ('rpc error: code = DeadlineExceeded desc = Volume '
                     'pvc-6ae55004 not ready, current state: CREATING')
_ABORTED_MESSAGE = ('rpc error: code = Aborted desc = An operation with the '
                    'given volume key modeInstance/us-central1/pvc-6ae55004/'
                    'vol1 already exists.\n --- Most likely a long process is '
                    'still running to completion. Retrying.')


class TestClassifyPvcFailure:
    """Whether a reported failure says anything about waiting longer.

    The codes come from the CreateVolume error table in the CSI spec and from
    external-provisioner's own checkError(); the messages are real.
    """

    def test_a_rejected_request_is_terminal(self):
        assert k8s_volume.classify_pvc_failure(
            _TERMINAL_MESSAGE) == k8s_volume.PvcFailure.TERMINAL

    @pytest.mark.parametrize('message', [_DEADLINE_MESSAGE, _ABORTED_MESSAGE])
    def test_a_call_that_may_still_be_running_is_not(self, message):
        """These are what a network filesystem reports while being created."""
        assert k8s_volume.classify_pvc_failure(
            message) == k8s_volume.PvcFailure.IN_PROGRESS

    @pytest.mark.parametrize('code',
                             ['AlreadyExists', 'OutOfRange', 'NotFound'])
    def test_the_other_codes_that_need_the_request_changed(self, code):
        assert k8s_volume.classify_pvc_failure(
            f'rpc error: code = {code} desc = nope'
        ) == k8s_volume.PvcFailure.TERMINAL

    @pytest.mark.parametrize('code', ['ResourceExhausted', 'Internal'])
    def test_an_obstacle_that_may_clear_is_left_undecided(self, code):
        """Quota can be raised while a launch waits, so neither hopeless nor
        provably in flight."""
        assert k8s_volume.classify_pvc_failure(
            f'rpc error: code = {code} desc = nope'
        ) == k8s_volume.PvcFailure.UNKNOWN

    @pytest.mark.parametrize('message', [
        None, '', 'failed to provision volume with StorageClass "slow"',
        'rpc error: something else entirely'
    ])
    def test_a_message_with_no_code_is_left_undecided(self, message):
        assert k8s_volume.classify_pvc_failure(
            message) == k8s_volume.PvcFailure.UNKNOWN

    def test_a_wrapped_status_is_read_by_its_inner_code(self):
        """grpc-go wraps a status in another status, and the outer one is
        usually the less specific of the two."""
        assert k8s_volume.classify_pvc_failure(
            'rpc error: code = Unknown desc = rpc error: '
            'code = InvalidArgument desc = bad tier'
        ) == k8s_volume.PvcFailure.TERMINAL

    def test_a_call_that_may_be_running_wins_over_a_terminal_wrapper(self):
        """The asymmetry that matters: waiting longer than needed costs time,
        failing a launch whose volume was going to work cannot be undone."""
        assert k8s_volume.classify_pvc_failure(
            'rpc error: code = InvalidArgument desc = rpc error: '
            'code = DeadlineExceeded desc = still creating'
        ) == k8s_volume.PvcFailure.IN_PROGRESS


class TestFirstPvcFailureEvent:
    """What the volume-status refresh counts as evidence a claim is unusable."""

    @staticmethod
    def _events(*messages):
        events = []
        for message in messages:
            event = MagicMock()
            event.type = 'Warning'
            event.reason = 'ProvisioningFailed'
            event.message = message
            events.append(event)
        return events

    def _first(self, monkeypatch, *messages):
        monkeypatch.setattr(k8s_volume.kubernetes_utils, 'get_pvc_events',
                            lambda *a, **kw: self._events(*messages))
        return k8s_volume._first_pvc_failure_event('ctx', 'ns', 'pvc')

    def test_a_rejected_request_is_evidence(self, monkeypatch):
        assert 'instance.tier' in self._first(monkeypatch, _TERMINAL_MESSAGE)

    def test_a_call_that_may_still_be_running_is_not(self, monkeypatch):
        """A volume being created reported these for minutes and then bound.
        Counting them made it read as unusable for the middle of its own
        creation, which refused every launch that wanted it."""
        assert self._first(monkeypatch, _DEADLINE_MESSAGE,
                           _ABORTED_MESSAGE) is None

    def test_a_real_failure_behind_an_in_progress_one_is_still_found(
            self, monkeypatch):
        assert 'instance.tier' in self._first(monkeypatch, _DEADLINE_MESSAGE,
                                              _TERMINAL_MESSAGE)


class TestObservedVolumeState:
    """What `get_all_volumes_state` reports about the cluster-owned fields.

    A volume's size is recorded when it is created, but the cluster can change
    it afterwards -- storage gets expanded, and a provisioner can round a
    request up -- so the refresh has to read it back from the PVC.
    """

    def _config(self, name_on_cloud='test-pvc', size='10', config=None):
        return models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud=name_on_cloud,
            size=size,
            config=config
            if config is not None else {'namespace': 'my-namespace'},
        )

    def _observe(self, fake_reads, pvc, config=None):
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [
            pvc
        ] if pvc is not None else []
        _, observed, _ = k8s_volume.get_all_volumes_state(
            [config if config is not None else self._config()])
        return observed

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_expanded_volume_reports_its_new_capacity(self, mock_get_context,
                                                      fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace', size='10Ti')
        pvc.status.phase = 'Bound'
        # An expansion moves the request first; status.capacity follows once
        # the volume has actually grown, and that is the size that exists.
        pvc.spec.resources.requests = {'storage': '25Ti'}
        pvc.status.capacity = {'storage': '25Ti'}

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].size == '25600'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_request_that_is_not_yet_fulfilled_is_not_a_capacity(
            self, mock_get_context, fake_reads):
        """A pending expansion must not be reported as the size on disk."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace', size='10Gi')
        pvc.status.phase = 'Bound'
        pvc.spec.resources.requests = {'storage': '20Gi'}
        pvc.status.capacity = {'storage': '10Gi'}

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].size == '10'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_an_unbound_claim_reports_no_capacity(self, mock_get_context,
                                                  fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace')
        pvc.status.phase = 'Pending'
        pvc.status.capacity = None

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].size is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_an_unreadable_capacity_reports_none(self, mock_get_context,
                                                 fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace')
        pvc.status.phase = 'Bound'
        pvc.status.capacity = {'storage': 'not-a-quantity'}

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].size is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_sub_gi_capacity_reports_none(self, mock_get_context, fake_reads):
        """Rounding to '0' is not a size a volume could be created with."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace')
        pvc.status.phase = 'Bound'
        pvc.status.capacity = {'storage': '100Mi'}

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].size is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_storage_class_is_reported(self, mock_get_context, fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace', storage_class='premium-rwo')
        pvc.status.phase = 'Bound'

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].storage_class_name == 'premium-rwo'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_no_storage_class_is_not_a_class_name(self, mock_get_context,
                                                  fake_reads):
        """'' opts out of dynamic provisioning; it is not a class to record."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace', storage_class='')
        pvc.status.phase = 'Bound'

        observed = self._observe(fake_reads, pvc)

        assert observed['test-vol'].storage_class_name is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_use_existing_pvc_is_observed_too(self, mock_get_context,
                                                fake_reads):
        """An adopted PVC carries no skypilot label, so it is read by name in
        the other branch -- which has to observe it all the same."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('adopted-pvc', 'my-namespace', size='25Ti')
        pvc.status.phase = 'Bound'
        pvc.metadata.labels = {}

        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = []
        fake_reads.gets[reads.PERSISTENT_VOLUME_CLAIMS] = pvc

        _, observed, _ = k8s_volume.get_all_volumes_state([
            self._config(name_on_cloud='adopted-pvc',
                         config={
                             'namespace': 'my-namespace',
                             'use_existing': True
                         })
        ])

        assert observed['test-vol'].size == '25600'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_an_unreadable_namespace_observes_nothing(self, mock_get_context,
                                                      fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = _read_error(None)

        _, observed, failed = k8s_volume.get_all_volumes_state([self._config()])

        assert observed == {}
        assert failed == {'test-vol'}

    def test_a_hostpath_volume_has_nothing_to_observe(self):
        config = models.VolumeConfig(
            _version=1,
            name='host-vol',
            type='k8s-hostpath',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='host-vol',
            size=None,
            config={'host_path': '/mnt/data'},
        )

        errors, observed, failed = k8s_volume.get_all_volumes_state([config])

        assert errors == {'host-vol': None}
        assert observed == {}
        assert not failed


class TestPvcSizeQuantities:
    """Sizes are read with Kubernetes' quantity grammar, not SkyPilot's.

    A claim SkyPilot did not create -- which is every `use_existing` volume --
    is written however its author wrote it, and the two grammars disagree on
    the two things most likely to appear: a bare number is bytes to Kubernetes
    and gibibytes to SkyPilot, and `2G` is a legal claim size that SkyPilot's
    parser does not recognise at all.
    """

    @pytest.mark.parametrize('quantity,expected', [
        ('10Gi', '10'),
        ('1Ti', '1024'),
        ('25Ti', '25600'),
        ('100Mi', None),
        ('2G', '2'),
        ('1G', '1'),
        ('500M', None),
        ('2T', '1863'),
        ('1e9', '1'),
        ('1000', None),
        ('512', None),
        ('1.5Gi', '2'),
        (None, None),
        ('', None),
        ('garbage', None),
    ])
    def test_a_quantity_reads_as_kubernetes_means_it(self, quantity, expected):
        assert k8s_volume._parse_pvc_size(quantity, 'test-pvc') == expected

    def test_a_bare_number_is_bytes_not_gibibytes(self):
        """The reading that would overstate a volume a billionfold."""
        assert k8s_volume._parse_pvc_size('10240', 'test-pvc') is None

    def test_a_decimal_suffix_is_read_rather_than_dropped(self):
        """`2G` is 2e9 bytes, which is 1.86GiB -- recorded as the nearest GiB
        rather than discarded, and not confused with 2Gi."""
        assert k8s_volume._parse_pvc_size('2G', 'test-pvc') == '2'
        assert k8s_volume._parse_pvc_size('2Gi', 'test-pvc') == '2'
        assert k8s_volume._parse_pvc_size('2000G', 'test-pvc') == '1863'
        assert k8s_volume._parse_pvc_size('2000Gi', 'test-pvc') == '2000'


class TestObservedResizeState:
    """What `get_all_volumes_state` reports about a resize in flight.

    A claim's capacity only moves once the new space exists, so a resize that
    is still running -- or that is waiting to be mounted -- is invisible in the
    size alone. It is read from the claim's conditions, which is also the only
    place the claim says anything about it in words.
    """

    def _config(self):
        return models.VolumeConfig(
            _version=1,
            name='test-vol',
            type='k8s-pvc',
            cloud='kubernetes',
            region='my-context',
            zone=None,
            name_on_cloud='test-pvc',
            size='1',
            config={'namespace': 'my-namespace'},
        )

    def _observe(self, fake_reads, pvc):
        fake_reads.lists[reads.PERSISTENT_VOLUME_CLAIMS] = [pvc]
        _, observed, _ = k8s_volume.get_all_volumes_state([self._config()])
        return observed['test-vol']

    def _condition(self, condition_type, message=None, status='True'):
        condition = Mock()
        condition.type = condition_type
        condition.status = status
        condition.message = message
        return condition

    def _pvc(self,
             conditions,
             allocated='2Gi',
             requested='2Gi',
             allocated_status=None):
        pvc = MockPVC('test-pvc', 'my-namespace', size='1Gi')
        pvc.status.phase = 'Bound'
        pvc.status.capacity = {'storage': '1Gi'}
        pvc.spec.resources.requests = {'storage': requested}
        pvc.status.allocated_resources = ({
            'storage': allocated
        } if allocated else None)
        pvc.status.allocated_resource_statuses = ({
            'storage': allocated_status
        } if allocated_status else None)
        pvc.status.conditions = conditions
        return pvc

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    @pytest.mark.parametrize('condition_type,expected', [
        ('Resizing', models.VolumeResizeStatus.IN_PROGRESS),
        ('FileSystemResizePending', models.VolumeResizeStatus.PENDING_ON_NODE),
        ('ControllerResizeError', models.VolumeResizeStatus.FAILED),
        ('NodeResizeError', models.VolumeResizeStatus.FAILED),
    ])
    def test_each_condition_reports_its_state(self, mock_get_context,
                                              condition_type, expected,
                                              fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')

        observed = self._observe(fake_reads,
                                 self._pvc([self._condition(condition_type)]))

        assert observed.resize_status == expected
        assert observed.resize_target_size == '2'
        # The size stays the capacity the volume actually has.
        assert observed.size == '1'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_waiting_on_the_node_outranks_resizing(self, mock_get_context,
                                                   fake_reads):
        """Both hold at once on a real claim, and `Resizing` is listed first."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([
            self._condition('Resizing'),
            self._condition('FileSystemResizePending', message='k8s says so'),
        ])

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status == (
            models.VolumeResizeStatus.PENDING_ON_NODE)
        assert observed.resize_message == 'k8s says so'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_failure_outranks_everything(self, mock_get_context, fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([
            self._condition('Resizing'),
            self._condition('FileSystemResizePending'),
            self._condition('NodeResizeError', message='quota exceeded'),
        ])

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status == models.VolumeResizeStatus.FAILED
        assert observed.resize_message == 'quota exceeded'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_the_words_come_from_the_condition_that_decided_the_state(
            self, mock_get_context, fake_reads):
        """Otherwise a message about one stage describes another."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([
            self._condition('Resizing', message='about the wrong state'),
            self._condition('FileSystemResizePending',
                            message='waiting to be mounted'),
        ])

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_message == 'waiting to be mounted'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_condition_without_words_reports_none(self, mock_get_context,
                                                    fake_reads):
        """Nothing is invented here; the caller supplies the fallback."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([self._condition('FileSystemResizePending')])

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status == (
            models.VolumeResizeStatus.PENDING_ON_NODE)
        assert observed.resize_message is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_condition_that_no_longer_holds_is_not_read(
            self, mock_get_context, fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([
            self._condition('FileSystemResizePending',
                            message='stale',
                            status='False'),
            self._condition('Resizing', message='current'),
        ])

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status == models.VolumeResizeStatus.IN_PROGRESS
        assert observed.resize_message == 'current'

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_allocated_resource_statuses_alone_reports_nothing(
            self, mock_get_context, fake_reads):
        """Deliberate: the conditions are the single source.

        Reading the state from allocatedResourceStatuses and the words from a
        condition lets the two describe different stages of the same resize --
        and every cluster has conditions, while only recent ones have that
        field.
        """
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([], allocated_status='NodeResizePending')

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status is None
        assert observed.resize_target_size is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_a_volume_that_is_not_being_resized_reports_nothing(
            self, mock_get_context, fake_reads):
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = MockPVC('test-pvc', 'my-namespace', size='1Gi')
        pvc.status.phase = 'Bound'

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_status is None
        assert observed.resize_target_size is None
        assert observed.resize_message is None

    @patch('sky.provision.kubernetes.volume._get_context_namespace')
    def test_the_target_falls_back_to_the_request(self, mock_get_context,
                                                  fake_reads):
        """A cluster that does not report allocatedResources still asked for
        something."""
        mock_get_context.return_value = ('my-context', 'my-namespace')
        pvc = self._pvc([self._condition('FileSystemResizePending')],
                        allocated=None,
                        requested='5Gi')

        observed = self._observe(fake_reads, pvc)

        assert observed.resize_target_size == '5'
