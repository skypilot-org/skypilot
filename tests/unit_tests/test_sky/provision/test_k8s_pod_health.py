"""Tests for Kubernetes pod health issue detection."""
from typing import Optional
from unittest import mock

from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes.instance import _check_nodes_health
from sky.provision.kubernetes.instance import _get_pod_health_issues


def _make_condition(type_: str,
                    status: str,
                    reason: str = '',
                    message: str = ''):
    """Create a mock pod condition."""
    cond = mock.MagicMock()
    cond.type = type_
    cond.status = status
    cond.reason = reason
    cond.message = message
    return cond


def _make_container_status(ready: bool,
                           waiting_reason: Optional[str] = None,
                           terminated_exit_code: Optional[int] = None,
                           name: str = 'ray-node'):
    """Create a mock container status."""
    cs = mock.MagicMock()
    cs.name = name
    cs.ready = ready

    if waiting_reason is not None:
        cs.state.waiting.reason = waiting_reason
        cs.state.terminated = None
    elif terminated_exit_code is not None:
        cs.state.terminated.exit_code = terminated_exit_code
        cs.state.terminated.reason = f'exit({terminated_exit_code})'
        cs.state.waiting = None
    else:
        cs.state.waiting = None
        cs.state.terminated = None
    return cs


def _make_pod(conditions, container_statuses=None):
    """Create a mock pod object."""
    pod = mock.MagicMock()
    pod.status.conditions = conditions
    pod.status.container_statuses = container_statuses or []
    return pod


class TestGetPodHealthIssues:
    """Tests for _get_pod_health_issues."""

    def test_healthy_pod_returns_none(self):
        pod = _make_pod(
            conditions=[_make_condition('Ready', 'True')],
            container_statuses=[_make_container_status(ready=True)],
        )
        assert _get_pod_health_issues(pod) is None

    def test_ready_false_returns_reason(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[_make_container_status(ready=False)],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'ContainersNotReady' in result

    def test_crashloopbackoff(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='CrashLoopBackOff'),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'CrashLoopBackOff' in result

    def test_image_pull_backoff(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='ImagePullBackOff'),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'ImagePullBackOff' in result

    def test_container_terminated_nonzero(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False, terminated_exit_code=137),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert '137' in result

    def test_no_conditions_returns_none(self):
        pod = _make_pod(conditions=None)
        assert _get_pod_health_issues(pod) is None

    def test_ready_true_but_container_waiting(self):
        pod = _make_pod(
            conditions=[_make_condition('Ready', 'True')],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='ContainerCreating'),
            ],
        )
        assert _get_pod_health_issues(pod) is None


def _make_node_info(is_ready: bool, is_cordoned: bool = False):
    """Create a mock KubernetesNodeInfo."""
    info = mock.MagicMock()
    info.is_ready = is_ready
    info.is_cordoned = is_cordoned
    return info


def _make_nodes_info(node_dict):
    """Create a mock KubernetesNodesInfo from {name: (ready, cordoned)}."""
    info = mock.MagicMock()
    info.node_info_dict = {
        name: _make_node_info(ready, cordoned)
        for name, (ready, cordoned) in node_dict.items()
    }
    return info


def _make_k8s_node(name: str, ready: bool):
    """Create a mock k8s V1Node for read_node fallback."""
    node = mock.MagicMock()
    node.metadata.name = name
    node.spec.unschedulable = False
    cond = mock.MagicMock()
    cond.type = 'Ready'
    cond.status = 'True' if ready else 'False'
    node.status.conditions = [cond]
    return node


class TestCheckNodesHealth:
    """Tests for _check_nodes_health."""

    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get')
    def test_node_info_source_detects_not_ready(self, mock_nis_get):
        mock_nis_get.return_value = _make_nodes_info({
            'node-1': (True, False),
            'node-2': (False, False),
        })
        result = _check_nodes_health('ctx', {'node-1', 'node-2'})
        assert 'node-2' in result
        assert 'NotReady' in result['node-2']
        assert 'node-1' not in result

    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get')
    def test_node_info_source_detects_cordoned(self, mock_nis_get):
        mock_nis_get.return_value = _make_nodes_info({
            'node-1': (True, True),
        })
        result = _check_nodes_health('ctx', {'node-1'})
        assert 'node-1' in result
        assert 'cordoned' in result['node-1']

    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get')
    def test_all_healthy_returns_empty(self, mock_nis_get):
        mock_nis_get.return_value = _make_nodes_info({
            'node-1': (True, False),
            'node-2': (True, False),
        })
        result = _check_nodes_health('ctx', {'node-1', 'node-2'})
        assert not result

    @mock.patch('sky.adaptors.kubernetes.core_api')
    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get',
                return_value=None)
    def test_fallback_to_k8s_api(self, mock_nis_get, mock_core_api):  # pylint: disable=unused-argument
        # Use a function for side_effect since run_in_parallel
        # may call read_node in any order.
        node_map = {
            'node-1': _make_k8s_node('node-1', ready=True),
            'node-2': _make_k8s_node('node-2', ready=False),
        }
        mock_core_api.return_value.read_node.side_effect = (
            lambda name, **kw: node_map[name])
        result = _check_nodes_health('ctx', {'node-1', 'node-2'})
        assert 'node-2' in result
        assert 'NotReady' in result['node-2']
        assert 'node-1' not in result

    @mock.patch('sky.adaptors.kubernetes.core_api')
    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get',
                return_value=None)
    def test_fallback_when_not_registered(self, mock_nis_get, mock_core_api):  # pylint: disable=unused-argument
        mock_core_api.return_value.read_node.return_value = _make_k8s_node(
            'node-1', ready=False)
        result = _check_nodes_health('ctx', {'node-1'})
        assert 'node-1' in result

    @mock.patch('sky.adaptors.kubernetes.core_api')
    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get',
                return_value=None)
    def test_fallback_read_node_exception_is_swallowed(
            self,
            mock_nis_get,  # pylint: disable=unused-argument
            mock_core_api):
        mock_core_api.return_value.read_node.side_effect = Exception('timeout')
        result = _check_nodes_health('ctx', {'node-1'})
        assert not result

    @mock.patch('sky.adaptors.kubernetes.core_api')
    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get',
                return_value=None)
    def test_fallback_detects_cordoned_node(self, mock_nis_get, mock_core_api):  # pylint: disable=unused-argument
        """K8s API fallback detects cordoned (unschedulable) nodes."""
        node = _make_k8s_node('node-1', ready=True)
        node.spec.unschedulable = True
        mock_core_api.return_value.read_node.return_value = node
        result = _check_nodes_health('ctx', {'node-1'})
        assert 'node-1' in result
        assert 'cordoned' in result['node-1']

    @mock.patch('sky.utils.plugin_extensions.NodeInfoSource.get')
    def test_filters_to_requested_nodes(self, mock_nis_get):
        mock_nis_get.return_value = _make_nodes_info({
            'node-1': (False, False),
            'node-2': (False, False),
            'node-3': (True, False),
        })
        result = _check_nodes_health('ctx', {'node-1'})
        assert 'node-1' in result
        assert 'node-2' not in result


def _make_full_pod(name: str,
                   phase: str,
                   node_name: str,
                   ready: bool = True,
                   waiting_reason: Optional[str] = None,
                   deletion_timestamp=None):
    """Create a pod object matching the Kubernetes API client format."""
    pod = mock.MagicMock()
    pod.metadata.name = name
    pod.metadata.deletion_timestamp = deletion_timestamp
    pod.metadata.labels = {}
    pod.status.phase = phase
    pod.spec.node_name = node_name

    ready_cond = _make_condition('Ready',
                                 'True' if ready else 'False',
                                 reason='' if ready else 'ContainersNotReady')
    pod.status.conditions = [ready_cond]
    pod.status.container_statuses = [
        _make_container_status(
            ready=ready,
            waiting_reason=waiting_reason if not ready else None,
        )
    ]
    return pod


class TestQueryInstancesHealthIntegration:
    """Integration tests for query_instances with pod health checks."""

    @mock.patch('sky.provision.kubernetes.instance.list_namespaced_pod')
    def test_running_unhealthy_pod_gets_reason(self, mock_list_pods):
        """query_instances sets reason for Running pods with Ready=False."""
        mock_list_pods.return_value = [
            _make_full_pod('head', 'Running', 'node-1', ready=True),
            _make_full_pod('worker-0', 'Running', 'node-2', ready=False),
        ]

        result = k8s_instance.query_instances(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cluster',
            provider_config={
                'namespace': 'default',
                'context': 'test-ctx',
                'services': [],
            },
        )
        # Head pod should have no reason
        assert result['head'][1] is None
        # Worker pod should have pod-level health reason
        assert result['worker-0'][1] is not None
        assert 'ContainersNotReady' in result['worker-0'][1]

    @mock.patch('sky.provision.kubernetes.instance.list_namespaced_pod')
    def test_healthy_pods_no_reason(self, mock_list_pods):
        """All healthy pods should have None reason."""
        mock_list_pods.return_value = [
            _make_full_pod('head', 'Running', 'node-1', ready=True),
            _make_full_pod('worker-0', 'Running', 'node-2', ready=True),
        ]

        result = k8s_instance.query_instances(
            cluster_name='test-cluster',
            cluster_name_on_cloud='test-cluster',
            provider_config={
                'namespace': 'default',
                'context': 'test-ctx',
                'services': [],
            },
        )
        assert result['head'][1] is None
        assert result['worker-0'][1] is None
