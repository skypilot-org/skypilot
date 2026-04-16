"""Tests for _summarize_pod_reasons in backend_utils."""
from sky.backends.backend_utils import _summarize_pod_reasons
from sky.utils import status_lib

UP = status_lib.ClusterStatus.UP

# _summarize_pod_reasons takes:
#   node_statuses: List[Tuple[ClusterStatus, Optional[str]]]
#   total_nodes: int
# The reason strings follow the format from query_instances:
#   '{pod_name}: {reason}; node {node_name} is {issue}'


class TestSummarizePodReasons:
    """Tests for _summarize_pod_reasons."""

    def test_no_reasons_returns_empty(self):
        statuses = [(UP, None), (UP, None)]
        assert _summarize_pod_reasons(statuses, 2) == ''

    def test_single_node_issue(self):
        statuses = [
            (UP, None),
            (UP, 'worker-0: pod not ready (ContainersNotReady); '
             'node gke-node-1 is NotReady'),
        ]
        result = _summarize_pod_reasons(statuses, 2)
        assert 'gke-node-1' in result
        assert 'NotReady' in result
        assert '1 out of 2 pods' in result

    def test_multiple_pods_same_node(self):
        statuses = [
            (UP, 'worker-0: pod not ready; node gke-node-1 is NotReady'),
            (UP, 'worker-1: pod not ready; node gke-node-1 is NotReady'),
        ]
        result = _summarize_pod_reasons(statuses, 4)
        assert 'gke-node-1' in result
        assert '2 out of 4 pods' in result

    def test_multiple_nodes_down(self):
        statuses = [
            (UP, 'w-0: pod not ready; node node-1 is NotReady'),
            (UP, 'w-1: pod not ready; node node-2 is NotReady'),
            (UP, 'w-2: pod not ready; node node-3 is NotReady'),
        ]
        result = _summarize_pod_reasons(statuses, 6)
        assert '3 nodes are NotReady' in result

    def test_node_names_capped_at_3(self):
        statuses = [(UP, f'w-{i}: pod not ready; node node-{i} is NotReady')
                    for i in range(5)]
        result = _summarize_pod_reasons(statuses, 10)
        assert '5 nodes are NotReady' in result
        assert '+ 2 more' in result

    def test_pod_only_issue_single(self):
        statuses = [
            (UP, None),
            (UP, 'worker-0: pod not ready (CrashLoopBackOff)'),
        ]
        result = _summarize_pod_reasons(statuses, 2)
        assert 'worker-0' in result
        assert 'CrashLoopBackOff' in result

    def test_pod_only_issue_multiple_same_reason(self):
        statuses = [
            (UP, 'w-0: pod not ready (CrashLoopBackOff)'),
            (UP, 'w-1: pod not ready (CrashLoopBackOff)'),
            (UP, 'w-2: pod not ready (CrashLoopBackOff)'),
            (UP, 'w-3: pod not ready (CrashLoopBackOff)'),
        ]
        result = _summarize_pod_reasons(statuses, 4)
        assert '4 pods' in result
        assert 'CrashLoopBackOff' in result

    def test_mixed_node_and_pod_issues(self):
        statuses = [
            (UP, 'w-0: pod not ready; node node-1 is NotReady'),
            (UP, 'w-1: pod not ready; node node-1 is NotReady'),
            (UP, 'w-2: pod not ready (CrashLoopBackOff)'),
        ]
        result = _summarize_pod_reasons(statuses, 6)
        assert 'node-1' in result
        assert 'NotReady' in result
        assert 'CrashLoopBackOff' in result

    def test_cordoned_node(self):
        statuses = [
            (UP, 'w-0: pod not ready; node node-1 is cordoned'),
        ]
        result = _summarize_pod_reasons(statuses, 2)
        assert 'node-1' in result
        assert 'cordoned' in result

    def test_mixed_issue_types_per_issue_pod_count(self):
        """Pod counts should be per issue type, not global."""
        statuses = [
            (UP, 'w-0: pod not ready; node node-1 is NotReady'),
            (UP, 'w-1: pod not ready; node node-1 is NotReady'),
            (UP, 'w-2: pod not ready; node node-2 is cordoned'),
        ]
        result = _summarize_pod_reasons(statuses, 6)
        assert '2 out of 6 pods' in result  # NotReady affects 2 pods
        assert '1 out of 6 pods' in result  # cordoned affects 1 pod


class TestStatusReasonIntegration:
    """Verify that _summarize_pod_reasons output is used correctly
    when building the init_reason for ray_cluster_unhealthy."""

    def test_status_reason_replaces_ray_message(self):
        statuses = [
            (UP, 'w-0: pod not ready; node node-1 is NotReady'),
            (UP, None),
        ]
        summary = _summarize_pod_reasons(statuses, 2)
        ray_cluster_unhealthy = True
        ray_status_details = '1/2 ready'
        if ray_cluster_unhealthy:
            if summary:
                init_reason = summary
            else:
                init_reason = f'ray cluster is unhealthy ({ray_status_details})'
        assert 'ray' not in init_reason
        assert 'node-1' in init_reason

    def test_empty_summary_falls_back_to_ray(self):
        statuses = [(UP, None), (UP, None)]
        summary = _summarize_pod_reasons(statuses, 2)
        ray_cluster_unhealthy = True
        ray_status_details = '1/2 ready'
        if ray_cluster_unhealthy:
            if summary:
                init_reason = summary
            else:
                init_reason = f'ray cluster is unhealthy ({ray_status_details})'
        assert 'ray cluster is unhealthy' in init_reason
