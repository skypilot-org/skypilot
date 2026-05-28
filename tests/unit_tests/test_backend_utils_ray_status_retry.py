"""Tests for the SSH-based ray-status retry inside _update_cluster_status.

The `for i in range(5)` loop in
`_update_cluster_status.run_ray_status_to_check_ray_cluster_healthy` was
previously only meaningful for kubernetes — non-k8s clouds raised on the
first `CommandError` and never iterated. That made a single SSH 255
(e.g. an SSM-tunnelled banner-exchange timeout on AWS) tear the cluster
down and trigger full re-provisioning. The retry now applies to all
clouds; structural failures (`Ray cluster is not found`) still
short-circuit.

Generalises the precedent from #6298.
"""
import time
from unittest import mock

from sky import backends
from sky import clouds
from sky import exceptions
from sky.backends import backend_utils
from sky.utils import status_lib

_SSH_255_STDERR = ('ssh: connect to host 10.0.0.1 port 22: '
                   'Connection timed out during banner exchange')
_RAY_CLUSTER_NOT_FOUND_STDERR = (
    'Failed to check ray cluster\'s healthiness.\n'
    '-- stdout --\n'
    'Ray cluster is not found at /tmp/ray/session_latest/sockets/raylet\n')


def _make_aws_handle():
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'test-cluster'
    handle.cluster_name_on_cloud = 'test-cluster-1234'
    handle.cluster_yaml = '/fake/path/cluster.yaml'
    handle.launched_nodes = 1
    handle.num_ips_per_node = 1
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = clouds.AWS()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _make_record(handle):
    return {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_hash': 'fake-hash',
        'autostop': -1,
        'to_down': False,
        'launched_at': time.time() - 3600,
    }


def _drive_update_cluster_status(get_node_counts_side_effect, handle=None):
    """Drive _update_cluster_status with a scripted ray-status outcome.

    Returns a dict with:
      - 'final_status': the ClusterStatus added via add_cluster_event
      - 'init_message': the message for the INIT event, if any
      - 'call_count': how many times the (mocked) ray-status call ran
      - 'sleep_calls': how many time.sleep(1) calls happened inside the
                       retry loop
    """
    if handle is None:
        handle = _make_aws_handle()
    record = _make_record(handle)

    head_runner = mock.Mock()
    handle.get_command_runners.return_value = [head_runner]

    captured = {
        'final_status': None,
        'init_message': None,
        'sleep_calls': 0,
    }

    def _capture_event(cluster_name, new_status, message, event_type, **kwargs):
        del cluster_name, event_type, kwargs
        captured['final_status'] = new_status
        if new_status == status_lib.ClusterStatus.INIT:
            captured['init_message'] = message

    node_statuses = {'i-0': (status_lib.ClusterStatus.UP, None)}

    def _counting_sleep(seconds):
        # Only count the 1s retry sleeps; ignore any other sleeps the
        # code may incidentally do. We don't actually sleep in tests.
        if seconds == 1:
            captured['sleep_calls'] += 1

    # `get_node_counts_from_ray_status` is nested inside
    # `_update_cluster_status`, so we patch the inner runner's `run`
    # method instead. The nested helper calls
    # `runner.run(RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND, ...)`, unpacks
    # `(rc, output, stderr)`, and raises CommandError when rc != 0.
    # Returning a tuple gives the happy path; raising CommandError
    # directly from `runner.run` gives the failure path with a custom
    # error_msg so the `Ray cluster is not found` branch can be tested.
    head_runner.run.side_effect = get_node_counts_side_effect

    with mock.patch.object(backend_utils,
                           '_query_cluster_status_via_cloud_api',
                           return_value=node_statuses), \
         mock.patch.object(backend_utils.ExternalFailureSource,
                           'get', return_value=None), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_cluster_event',
                           side_effect=_capture_event), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster'), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_from_name',
                           return_value=record), \
         mock.patch.object(backend_utils, '_count_healthy_nodes_from_ray',
                           return_value=(1, 0)), \
         mock.patch.object(backend_utils.time, 'sleep',
                           side_effect=_counting_sleep), \
         mock.patch.object(backend_utils, 'get_backend_from_handle',
                           return_value=None):
        backend_utils._update_cluster_status('test-cluster',
                                             record,
                                             retry_if_missing=False)

    captured['call_count'] = head_runner.run.call_count
    return captured


def _ssh_255_side_effect(num_failures: int):
    """Returns a side_effect list: `num_failures` SSH 255s, then success.

    Success returns (rc=0, output='', stderr='') — combined with the
    `_count_healthy_nodes_from_ray` patch returning (1, 0), this makes
    `ready_head + ready_workers == total_nodes` and the loop returns True.
    """
    failures = [(255, '', _SSH_255_STDERR)] * num_failures
    success = (0, '', '')
    return failures + [success]


class TestRayStatusRetry:
    """Retry behavior for run_ray_status_to_check_ray_cluster_healthy."""

    def test_aws_retries_transient_ssh_failures(self):
        # 4 SSH 255s, 5th attempt succeeds. The cluster should stay UP
        # and the loop should iterate (4 sleeps between attempts).
        result = _drive_update_cluster_status(_ssh_255_side_effect(4))
        assert result['final_status'] == status_lib.ClusterStatus.UP
        assert result['call_count'] == 5
        assert result['sleep_calls'] == 4

    def test_aws_retries_then_gives_up_after_5_attempts(self):
        # All 5 attempts fail with SSH 255. Cluster transitions to INIT.
        # Regression check: prior to this change the loop exited on the
        # very first failure (call_count would be 1).
        result = _drive_update_cluster_status([(255, '', _SSH_255_STDERR)] * 5)
        assert result['final_status'] == status_lib.ClusterStatus.INIT
        assert result['call_count'] == 5
        assert result['sleep_calls'] == 5

    def test_aws_ray_runtime_not_found_short_circuits(self):
        # `Ray cluster is not found` is a structural failure (ray daemon
        # really is gone). Should NOT retry — fail fast and surface the
        # warning.
        err = exceptions.CommandError(
            returncode=1,
            command='ray status',
            error_msg=_RAY_CLUSTER_NOT_FOUND_STDERR,
            detailed_reason=None,
        )
        result = _drive_update_cluster_status([err, err, err, err, err])
        assert result['final_status'] == status_lib.ClusterStatus.INIT
        assert result['call_count'] == 1, (
            'Expected no retry on "Ray cluster is not found"; got '
            f'{result["call_count"]} attempts')
        assert result['sleep_calls'] == 0

    def test_aws_succeeds_on_first_attempt_no_retry(self):
        # Happy path: no failures, no retries, cluster stays UP.
        result = _drive_update_cluster_status([(0, '', '')])
        assert result['final_status'] == status_lib.ClusterStatus.UP
        assert result['call_count'] == 1
        assert result['sleep_calls'] == 0

    def test_kubernetes_retry_unchanged(self):
        # Sanity: k8s still retries (its existing behavior). 4 failures
        # then success.
        handle = _make_aws_handle()
        handle.launched_resources.cloud = clouds.Kubernetes()
        result = _drive_update_cluster_status(_ssh_255_side_effect(4),
                                              handle=handle)
        assert result['final_status'] == status_lib.ClusterStatus.UP
        assert result['call_count'] == 5
        assert result['sleep_calls'] == 4
