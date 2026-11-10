"""Tests for the summarize helpers in backend_utils."""
from sky import exceptions
from sky.backends.backend_utils import _summarize_pod_reasons
from sky.backends.backend_utils import _summarize_probe_failure
from sky.provision.kubernetes.instance import NodeHealthInfo
from sky.utils import status_lib

UP = status_lib.ClusterStatus.UP

N = NodeHealthInfo  # Short alias for readability


class TestSummarizePodReasons:
    """Tests for _summarize_pod_reasons."""

    def test_no_reasons_returns_empty(self):
        statuses = {'head': (UP, None), 'worker-0': (UP, None)}
        assert _summarize_pod_reasons(statuses, 2) == ''

    def test_single_node_issue(self):
        statuses = {
            'head': (UP, None),
            'worker-0': (UP, 'pod not ready (ContainersNotReady)'),
        }
        node_health = {
            'gke-node-1': N(issue='NotReady', pods=['worker-0']),
        }
        result = _summarize_pod_reasons(statuses, 2, node_health)
        assert 'gke-node-1' in result
        assert 'NotReady' in result
        assert '1 out of 2 pods' in result

    def test_multiple_pods_same_node(self):
        statuses = {
            'worker-0': (UP, 'pod not ready'),
            'worker-1': (UP, 'pod not ready'),
        }
        node_health = {
            'gke-node-1': N(issue='NotReady', pods=['worker-0', 'worker-1']),
        }
        result = _summarize_pod_reasons(statuses, 4, node_health)
        assert 'gke-node-1' in result
        assert '2 out of 4 pods' in result

    def test_multiple_nodes_down(self):
        statuses = {
            'w-0': (UP, 'pod not ready'),
            'w-1': (UP, 'pod not ready'),
            'w-2': (UP, 'pod not ready'),
        }
        node_health = {
            'node-1': N(issue='NotReady', pods=['w-0']),
            'node-2': N(issue='NotReady', pods=['w-1']),
            'node-3': N(issue='NotReady', pods=['w-2']),
        }
        result = _summarize_pod_reasons(statuses, 6, node_health)
        assert '3 nodes are NotReady' in result

    def test_node_names_capped_at_3(self):
        statuses = {f'w-{i}': (UP, 'pod not ready') for i in range(5)}
        node_health = {
            f'node-{i}': N(issue='NotReady', pods=[f'w-{i}']) for i in range(5)
        }
        result = _summarize_pod_reasons(statuses, 10, node_health)
        assert '5 nodes are NotReady' in result
        assert '+ 2 more' in result

    def test_pod_only_issue_single(self):
        """Pod issue with no node health data."""
        statuses = {
            'head': (UP, None),
            'worker-0': (UP, 'pod not ready (CrashLoopBackOff)'),
        }
        result = _summarize_pod_reasons(statuses, 2)
        assert 'worker-0' in result
        assert 'CrashLoopBackOff' in result

    def test_pod_only_issue_multiple_same_reason(self):
        statuses = {
            f'w-{i}': (UP, 'pod not ready (CrashLoopBackOff)') for i in range(4)
        }
        result = _summarize_pod_reasons(statuses, 4)
        assert '4 pods' in result
        assert 'CrashLoopBackOff' in result

    def test_mixed_node_and_pod_issues(self):
        """Node issues + pod-only issues in same cluster."""
        statuses = {
            'w-0': (UP, 'pod not ready'),
            'w-1': (UP, 'pod not ready'),
            'w-2': (UP, 'pod not ready (CrashLoopBackOff)'),
        }
        node_health = {
            'node-1': N(issue='NotReady', pods=['w-0', 'w-1']),
        }
        result = _summarize_pod_reasons(statuses, 6, node_health)
        assert 'node-1' in result
        assert 'NotReady' in result
        assert 'CrashLoopBackOff' in result

    def test_cordoned_node(self):
        statuses = {
            'w-0': (UP, 'pod not ready'),
        }
        node_health = {
            'node-1': N(issue='cordoned', pods=['w-0']),
        }
        result = _summarize_pod_reasons(statuses, 2, node_health)
        assert 'node-1' in result
        assert 'cordoned' in result

    def test_mixed_issue_types_per_issue_pod_count(self):
        """Pod counts should be per issue type, not global."""
        statuses = {
            'w-0': (UP, 'pod not ready'),
            'w-1': (UP, 'pod not ready'),
            'w-2': (UP, 'pod not ready'),
        }
        node_health = {
            'node-1': N(issue='NotReady', pods=['w-0', 'w-1']),
            'node-2': N(issue='cordoned', pods=['w-2']),
        }
        result = _summarize_pod_reasons(statuses, 6, node_health)
        assert '2 out of 6 pods' in result
        assert '1 out of 6 pods' in result

    def test_node_explained_pods_excluded_from_pod_summary(self):
        """Pods explained by node issues should not appear in pod section."""
        statuses = {
            'w-0': (UP, 'pod not ready (ContainersNotReady)'),
            'w-1': (UP, 'pod not ready (CrashLoopBackOff)'),
        }
        node_health = {
            'node-1': N(issue='NotReady', pods=['w-0']),
        }
        result = _summarize_pod_reasons(statuses, 4, node_health)
        assert 'node-1' in result
        assert 'CrashLoopBackOff' in result
        parts = result.split('; ')
        assert len(parts) == 2


class TestStatusReasonIntegration:
    """Verify that _summarize_pod_reasons output is used correctly
    when building the init_reason for ray_cluster_unhealthy."""

    def test_status_reason_replaces_ray_message(self):
        statuses = {
            'w-0': (UP, 'pod not ready (ContainersNotReady)'),
            'head': (UP, None),
        }
        node_health = {
            'node-1': N(issue='NotReady', pods=['w-0']),
        }
        summary = _summarize_pod_reasons(statuses, 2, node_health)
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
        statuses = {'head': (UP, None), 'worker': (UP, None)}
        summary = _summarize_pod_reasons(statuses, 2)
        ray_cluster_unhealthy = True
        ray_status_details = '1/2 ready'
        if ray_cluster_unhealthy:
            if summary:
                init_reason = summary
            else:
                init_reason = f'ray cluster is unhealthy ({ray_status_details})'
        assert 'ray cluster is unhealthy' in init_reason


class TestSummarizeProbeFailure:
    """Tests for _summarize_probe_failure."""

    def _make_error(self,
                    detailed_reason,
                    error_msg='Failed to check ray '
                    'cluster\'s healthiness.\n-- stdout --\n\n'):
        return exceptions.CommandError(255, 'x' * 700, error_msg,
                                       detailed_reason)

    def test_uses_last_stderr_line(self):
        e = self._make_error(
            'mux_client_request_session: read from master failed: '
            'Broken pipe\n'
            'ssh: connect to host 1.2.3.4 port 22: Operation timed out\n')
        result = _summarize_probe_failure(e)
        assert result == ('health probe failed: ssh: connect to host '
                          '1.2.3.4 port 22: Operation timed out')

    def test_connection_refused(self):
        e = self._make_error(
            'ssh: connect to host 1.2.3.4 port 22: Connection refused')
        result = _summarize_probe_failure(e)
        assert result == ('health probe failed: ssh: connect to host '
                          '1.2.3.4 port 22: Connection refused')

    def test_excludes_remote_command(self):
        # str(e) embeds the (long) remote command; the summary must not.
        e = self._make_error('ssh: connect to host 1.2.3.4 port 22: '
                             'Connection refused')
        result = _summarize_probe_failure(e)
        assert 'x' * 100 not in result
        assert len(result) < 200

    def test_falls_back_to_error_msg(self):
        e = self._make_error(None, error_msg='Ray cluster is not found.')
        result = _summarize_probe_failure(e)
        assert result == 'health probe failed: Ray cluster is not found.'

    def test_whitespace_stderr_falls_back(self):
        e = self._make_error('  \n \n', error_msg='some error')
        result = _summarize_probe_failure(e)
        assert result == 'health probe failed: some error'

    def test_no_detail_uses_return_code(self):
        e = self._make_error(None, error_msg='')
        result = _summarize_probe_failure(e)
        assert result == 'health probe failed with return code 255'
