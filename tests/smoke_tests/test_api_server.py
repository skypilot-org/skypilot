import sys
import tempfile
import threading
from typing import Dict, List, Optional, Tuple, TypeVar

import pytest
from smoke_tests import metrics_utils
from smoke_tests import smoke_tests_utils

import sky
from sky.client import common as client_common
from sky.server import common as server_common
from sky.skylet import constants

T = TypeVar('T')


def set_user(user_id: str, user_name: str, commands: List[str]) -> List[str]:
    return [
        f'export {constants.USER_ID_ENV_VAR}="{user_id}"; '
        f'export {constants.USER_ENV_VAR}="{user_name}"; ' + cmd
        for cmd in commands
    ]


# ---------- Test multi-tenant ----------
@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant jobs
@pytest.mark.no_seeweb  # Seeweb does not support multi-tenant jobs
def test_multi_tenant(generic_cloud: str):
    if smoke_tests_utils.services_account_token_configured_in_env_file():
        pytest.skip(
            'Skipping multi-tenant test because a service account token is '
            'configured. The service account token represents a unique user, '
            'so USER_ENV_VAR cannot be used to simulate multiple users.')

    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef12'
    user_1_name = 'user1'
    user_2 = 'abcdef13'
    user_2_name = 'user2'

    stop_test_cmds = [
        'echo "==== Test multi-tenant cluster stop ===="',
        *set_user(
            user_2,
            user_2_name,
            [
                f'sky stop -y -a',
                # -a should only stop clusters from the current user.
                f's=$(sky status -u {name}-1) && echo "$s" && echo "$s" | grep {user_1_name} | grep UP',
                f's=$(sky status -u {name}-2) && echo "$s" && echo "$s" | grep {user_2_name} | grep STOPPED',
                # Explicit cluster name should stop the cluster.
                f'sky stop -y {name}-1',
                # Stopping cluster should not change the ownership of the cluster.
                f's=$(sky status) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                f'sky status {name}-1 | grep STOPPED',
                # Both clusters should be stopped.
                f'sky status -u | grep {name}-1 | grep STOPPED',
                f'sky status -u | grep {name}-2 | grep STOPPED',
            ]),
    ]
    if generic_cloud == 'kubernetes':
        # Skip the stop test for Kubernetes, as stopping is not supported.
        stop_test_cmds = []

    test = smoke_tests_utils.Test(
        'test_multi_tenant',
        [
            'echo "==== Test multi-tenant job on single cluster ===="',
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n job-1 tests/test_yamls/minimal.yaml',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 | grep SUCCEEDED | awk \'{{print $1}}\' | grep 1',
                f's=$(sky queue -u {name}-1) && echo "$s" && echo "$s" | grep {user_1_name} | grep job-1 | grep SUCCEEDED',
            ]),
            *set_user(user_2, user_2_name, [
                f'sky exec {name}-1 -n job-2 \'echo "hello" && exit 1\' || [ $? -eq 100 ]',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-2 | grep FAILED | awk \'{{print $1}}\' | grep 2',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 && exit 1 || true',
                f's=$(sky queue {name}-1 -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep job-2 | grep FAILED',
                f's=$(sky queue {name}-1 -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep job-1 | grep SUCCEEDED',
            ]),
            'echo "==== Test clusters from different users ===="',
            *set_user(
                user_2,
                user_2_name,
                [
                    f'sky launch -y -c {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n job-3 tests/test_yamls/minimal.yaml',
                    f's=$(sky status {name}-2) && echo "$s" && echo "$s" | grep UP',
                    # sky status should not show other clusters from other users.
                    f's=$(sky status) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                    # Explicit cluster name should show the cluster.
                    f's=$(sky status {name}-1) && echo "$s" && echo "$s" | grep UP',
                    f's=$(sky status -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep {name}-2 | grep UP',
                    f's=$(sky status -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep UP',
                ]),
            *stop_test_cmds,
            'echo "==== Test multi-tenant cluster down ===="',
            *set_user(
                user_2,
                user_2_name,
                [
                    f'sky down -y -a',
                    # STOPPED or UP based on whether we run the stop_test_cmds.
                    f'sky status -u | grep {name}-1 | grep "STOPPED\|UP"',
                    # Current user's clusters should be down'ed.
                    f'sky status -u | grep {name}-2 && exit 1 || true',
                    # Explicit cluster name should delete the cluster.
                    f'sky down -y {name}-1',
                    f'sky status | grep {name}-1 && exit 1 || true',
                ]),
        ],
        f'sky down -y {name}-1 {name}-2',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant jobs
@pytest.mark.no_seeweb  # Seeweb does not support multi-tenant jobs
def test_multi_tenant_managed_jobs(generic_cloud: str):
    if smoke_tests_utils.services_account_token_configured_in_env_file():
        pytest.skip(
            'Skipping multi-tenant test because a service account token is '
            'configured. The service account token represents a unique user, '
            'so USER_ENV_VAR cannot be used to simulate multiple users.')

    name = smoke_tests_utils.get_cluster_name()
    user_1 = 'abcdef12'
    user_1_name = 'user1'
    user_2 = 'abcdef13'
    user_2_name = 'user2'

    controller_related_test_cmds = []
    # Only enable controller related tests for non-consolidation mode.
    # For consolidation mode, the controller is the same with API server,
    # hence no `sky status` output is available nor controller down is supported.
    if not smoke_tests_utils.server_side_is_consolidation_mode():
        controller_related_test_cmds = [
            'echo "==== Test jobs controller cluster user ===="',
            f's=$(sky status -u) && echo "$s" && echo "$s" | grep sky-jobs-controller- | grep -v {user_1_name} | grep -v {user_2_name}',
            'echo "==== Test controller down blocked by other users ===="',
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-3 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y -d sleep 1000',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-3',
                    job_status=[
                        sky.ManagedJobStatus.PENDING,
                        sky.ManagedJobStatus.DEPRECATED_SUBMITTED,
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=60),
            ]),
            *set_user(user_2, user_2_name, [
                f'controller=$(sky status -u | grep sky-jobs-controller- | awk \'{{print $1}}\') && echo "$controller" && echo delete | sky down "$controller" && exit 1 || true',
                f'sky jobs cancel -y -n {name}-3',
            ]),
        ]

    test = smoke_tests_utils.Test(
        'test_multi_tenant_managed_jobs',
        [
            'echo "==== Test multi-tenant managed jobs ===="',
            *set_user(user_1, user_1_name, [
                f'sky jobs launch -n {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml -y',
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-1 | grep SUCCEEDED',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep SUCCEEDED',
            ]),
            *set_user(user_2, user_2_name, [
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_1_name} | grep {name}-1 | grep SUCCEEDED',
                f'sky jobs launch -n {name}-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml -y',
                f's=$(sky jobs queue) && echo "$s" && echo "$s" | grep {name}-2 | grep SUCCEEDED',
                f's=$(sky jobs queue -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep {name}-2 | grep SUCCEEDED',
            ]),
            'echo "==== Test cancellation ===="',
            *set_user(user_1, user_1_name, [
                f'sky jobs launch --async -n {name}-cancel-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} sleep 300 -y',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-cancel-1',
                    job_status=[
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=60),
            ]),
            *set_user(
                user_2,
                user_2_name,
                [
                    f'sky jobs launch --async -n {name}-cancel-2 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} sleep 300 -y',
                    smoke_tests_utils.
                    get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                        job_name=f'{name}-cancel-2',
                        job_status=[
                            sky.ManagedJobStatus.STARTING,
                            sky.ManagedJobStatus.RUNNING
                        ],
                        timeout=60),
                    # Should only cancel user_2's job.
                    'sky jobs cancel -y --all',
                    smoke_tests_utils.
                    get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                        job_name=f'{name}-cancel-2',
                        job_status=[
                            sky.ManagedJobStatus.CANCELLED,
                            sky.ManagedJobStatus.CANCELLING
                        ],
                        timeout=60),
                    # Should cancel user_1's job.
                    'sky jobs cancel -y --all-users'
                ]),
            *set_user(user_1, user_1_name, [
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}-cancel-1',
                    job_status=[
                        sky.ManagedJobStatus.CANCELLED,
                        sky.ManagedJobStatus.CANCELLING
                    ],
                    timeout=60),
            ]),
            *controller_related_test_cmds,
        ],
        f'sky jobs cancel -y -n {name}-1; sky jobs cancel -y -n {name}-2; sky jobs cancel -y -n {name}-3',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# Test request scheduling in different API server deployment modes, currently
# we rely on --remote-server flag to test the two different scenarios:
# 1. with --remote-server: API server is deployed remotely with `--deploy` flag
# 2. without --remote-server: API server is launched before smoke test using
#    `sky api start`, same as the server being automatically launched for the
#    first time user running sky command.
# TODO(aylei): test different modes without relying on global smoke test setup
# when we can launch isolated API server instance in each case.
def test_requests_scheduling(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    user = "abcdef21"
    username = f"dispatch-{smoke_tests_utils.test_id}"
    test = smoke_tests_utils.Test(
        'test_requests_scheduling',
        [
            'echo "==== Test queue dispatch ===="',
            'sky api info',
            *set_user(
                user,
                username,
                [
                    f'sky launch -y -c {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n dispatch tests/test_yamls/minimal.yaml',
                    f'for i in {{1..10}}; do sky exec {name} -n job-${{i}} "sleep 60" --async; done',
                    # Wait for all reqeusts get scheduled and executed, do not check the status here to make this case focus.
                    (f's=$(sky api status -a -l none | grep {username});'
                     'timeout=60; start=$SECONDS; '
                     'until ! echo "$s" | grep "" | grep "PENDING|RUNNING"; do '
                     '  if [ $((SECONDS - start)) -gt $timeout ]; then '
                     '    echo "Timeout waiting for jobs to be scheduled"; exit 1; '
                     '  fi; '
                     f'  sleep 5; s=$(sky api status -a -l none | grep {username});'
                     '  echo "Waiting for request get scheduled"; echo "$s"; '
                     'done'),
                ],
            ),
        ],
        f'sky down -y {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# ---- Test recent request tracking -----
def test_recent_request_tracking(generic_cloud: str):
    with smoke_tests_utils.override_sky_config():
        # We need to override the sky api endpoint env if --remote-server is
        # specified, so we can run the test on the remote server.
        name = smoke_tests_utils.get_cluster_name()
        task = sky.Task(run="whoami")
        task.set_resources(
            sky.Resources(infra=generic_cloud,
                          **smoke_tests_utils.LOW_RESOURCE_PARAM))
        try:
            # launch two jobs
            req_id = sky.launch(task, cluster_name=name)
            sky.get(req_id)
            req_id_exec = sky.exec(task, cluster_name=name)
            sky.get(req_id_exec)

            params = {
                'request_id': None,
                'log_path': None,
                'tail': None,
                'follow': True,
                'format': 'console',
            }
            response = server_common.make_authenticated_request(
                'GET',
                '/api/stream',
                params=params,
                retry=False,
                timeout=(
                    client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
                    None),
                stream=True)
            stream_request_id: Optional[server_common.RequestId[
                T]] = server_common.get_stream_request_id(response)
            assert req_id_exec == stream_request_id
        finally:
            sky.get(sky.down(name))


@pytest.mark.no_hyperbolic  # Hyperbolic does not support managed jobs
@pytest.mark.no_seeweb  # Seeweb does not support managed jobs
def test_managed_jobs_force_disable_cloud_bucket(generic_cloud: str):
    """Test jobs with force_disable_cloud_bucket config.

    This tests the "two-hop" scenario where:
    1. Client submits job to remote API server (first translation with file_mounts_mapping)
    2. Jobs controller submits task back to API server (second translation)

    This is a regression test for a bug where file_mounts_mapping would persist
    in the task config after the first translation, causing KeyError on the
    second translation when the jobs controller calls back to the API server.
    """
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'test_managed_jobs_force_disable_cloud_bucket',
        [
            # Launch a managed job with force_disable_cloud_bucket config.
            # This forces the "two-hop" scenario where the task is submitted
            # twice to API servers, which would trigger the bug.
            f'sky jobs launch -n {name} --cloud {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--config jobs.force_disable_cloud_bucket=true '
            f'tests/test_yamls/minimal.yaml -y -d',
            # Wait for the job to complete successfully.
            # If the bug exists, this would fail with KeyError.
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=300),
        ],
        f'sky jobs cancel -y -n {name} || true',
    )
    smoke_tests_utils.run_one_test(test)


def test_big_file_upload_memory_usage(generic_cloud: str):
    if not smoke_tests_utils.is_remote_server_test():
        pytest.skip('This test is only for remote server')

    def compare_rss_metrics(baseline: Dict[Tuple[str, ...], List[Tuple[float,
                                                                       float]]],
                            actual: Dict[Tuple[str, ...], List[Tuple[float,
                                                                     float]]]):

        def _rss_peak_aggregator(
            baseline_values: List[Tuple[float, float]],
            actual_values: List[Tuple[float, float]]
        ) -> metrics_utils.AggregatedMetric:
            """Aggregator for RSS (memory) metrics - computes peak values."""
            baseline_peak_bytes = max([v for _, v in baseline_values
                                      ]) if baseline_values else 0
            actual_peak_bytes = max([v for _, v in actual_values
                                    ]) if actual_values else 0

            baseline_mb = baseline_peak_bytes / (1024 * 1024)
            actual_mb = actual_peak_bytes / (1024 * 1024)

            return metrics_utils.AggregatedMetric(baseline=baseline_mb,
                                                  actual=actual_mb,
                                                  unit='MB')

        def _rss_per_key_threshold_checker(key_label: str, baseline: float,
                                           actual: float, increase: float,
                                           increase_pct: float) -> List[str]:
            """Per-key threshold checker for RSS metrics."""
            failures = []
            if actual > 300:
                failures.append(f"exceeded 300 MB: {actual:.1f} MB")
            if increase_pct > 50 and increase_pct != float('inf'):
                failures.append(
                    f"increased by {increase_pct:.1f}% (limit: 50%)")
            return failures

        def _rss_aggregate_threshold_checker(
                total_baseline: float, total_actual: float,
                total_increase: float, total_increase_pct: float) -> List[str]:
            """Aggregate threshold checker for RSS metrics."""
            failures = []
            if total_increase_pct > 20:
                failures.append(
                    f"Average memory increase too high: {total_increase_pct:.1f}% (limit: 20%)"
                )
            return failures

        metrics_utils.compare_metrics(
            baseline,
            actual,
            aggregator_fn=_rss_peak_aggregator,
            per_key_threshold_fn=_rss_per_key_threshold_checker,
            aggregate_threshold_fn=_rss_aggregate_threshold_checker)

    with smoke_tests_utils.override_sky_config():
        name = smoke_tests_utils.get_cluster_name()

        metrics_server_url = smoke_tests_utils.get_metrics_server_url()
        metrics_url = f'{metrics_server_url}/metrics'

        print("Collecting baseline RSS measurements...",
              file=sys.stderr,
              flush=True)
        baseline_metrics = metrics_utils.collect_metrics(
            metrics_url, 'sky_apiserver_process_peak_rss', duration_seconds=30)

        # Create large files and launch task.
        with tempfile.NamedTemporaryFile(mode='wb') as large_file1, \
            tempfile.NamedTemporaryFile(mode='wb') as large_file2, \
            tempfile.NamedTemporaryFile(mode='wb') as large_file3:
            smoke_tests_utils.write_blob(large_file1, 1024 * 1024 * 1024)
            smoke_tests_utils.write_blob(large_file2, 1024 * 1024 * 1024)
            smoke_tests_utils.write_blob(large_file3, 1024 * 1024 * 1024)

            req_id = sky.launch(task=sky.Task(
                run='ls -l /large_file1 /large_file2 /large_file3',
                resources=sky.Resources(infra=generic_cloud,
                                        **smoke_tests_utils.LOW_RESOURCE_PARAM),
                file_mounts={
                    '/large_file1': large_file1.name,
                    '/large_file2': large_file2.name,
                    '/large_file3': large_file3.name,
                }),
                                cluster_name=name)

            # Start metrics collection in background thread.
            actual_metrics = {}
            stop_metrics = threading.Event()

            def collect_actual_metrics():
                nonlocal actual_metrics
                actual_metrics = metrics_utils.collect_metrics(
                    metrics_url,
                    'sky_apiserver_process_peak_rss',
                    stop_event=stop_metrics)

            print("Starting actual RSS measurement...",
                  file=sys.stderr,
                  flush=True)
            metrics_thread = threading.Thread(target=collect_actual_metrics)
            metrics_thread.start()

            sky.stream_and_get(req_id, output_stream=sys.stderr)

            # Cleanup the cluster.
            req_id = sky.down(name)
            sky.stream_and_get(req_id, output_stream=sys.stderr)

            stop_metrics.set()
            metrics_thread.join(timeout=10)

            assert len(baseline_metrics) > 0, "No baseline metrics collected"
            assert len(actual_metrics) > 0, "No actual metrics collected"

            compare_rss_metrics(baseline_metrics, actual_metrics)


# TODO(aylei): this case should not be retried in buildkite.
def test_api_server_start_stop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'test_managed_jobs_force_disable_cloud_bucket',
        [
            # To avoid interference with other tests, we launch a separate API server for this test.
            f'sky launch -n {name} --cloud {generic_cloud} tests/test_yamls/apiserver-start-stop.yaml -y {smoke_tests_utils.LOW_RESOURCE_ARG}'
        ],
        f'sky down -y {name} || true',
    )
    smoke_tests_utils.run_one_test(test)
