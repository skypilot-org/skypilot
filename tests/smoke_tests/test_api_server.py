import asyncio
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, Generator, List, Optional, Tuple, TypeVar

import pytest
import requests
from smoke_tests import metrics_utils
from smoke_tests import smoke_tests_utils
import websockets

import sky
from sky import jobs
from sky import skypilot_config
from sky.client import common as client_common
from sky.server import common as server_common
from sky.skylet import constants
from sky.utils import context

T = TypeVar('T')


def set_user(user_id: str, user_name: str, commands: List[str]) -> List[str]:
    return [
        f'export {constants.USER_ID_ENV_VAR}="{user_id}"; '
        f'export {constants.USER_ENV_VAR}="{user_name}"; ' + cmd
        for cmd in commands
    ]


# ---------- Test multi-tenant ----------
@pytest.mark.no_hyperbolic  # Hyperbolic does not support multi-tenant jobs
@pytest.mark.no_shadeform  # Shadeform does not support multi-tenant jobs
@pytest.mark.no_seeweb  # Seeweb does not support multi-tenant jobs
# Note: we should skip or fix on shared remote cluster because two copies of
# this test may down each other's clusters (sky down -a with hardcoded user id).
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
                # Restarting other user's cluster should work.
                f'sky start -y {name}-1',
                # Cluster should still have the same disk.
                f'sky exec {name}-1 \'ls file || exit 1\'',
                # Restarting cluster should not change the ownership of the cluster.
                f's=$(sky status) && echo "$s" && echo "$s" | grep {name}-1 && exit 1 || true',
                # Cluster 1 should be UP now, but cluster 2 should be STOPPED.
                f'sky status -u | grep {name}-1 | grep UP',
                f'sky status -u | grep {name}-2 | grep STOPPED',
            ]),
    ]
    if generic_cloud in ('kubernetes', 'slurm'):
        # Skip the stop test for Kubernetes and Slurm, as stopping is not supported.
        stop_test_cmds = []

    test = smoke_tests_utils.Test(
        'test_multi_tenant',
        [
            'echo "==== Test multi-tenant job on single cluster ===="',
            *set_user(user_1, user_1_name, [
                f'sky launch -y -c {name}-1 --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -n job-1 tests/test_yamls/minimal.yaml',
                f'sky exec {name}-1 -n job-2 \'touch file\'',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 | grep SUCCEEDED | awk \'{{print $1}}\' | grep 1',
                f's=$(sky queue -u {name}-1) && echo "$s" && echo "$s" | grep {user_1_name} | grep job-1 | grep SUCCEEDED',
            ]),
            *set_user(user_2, user_2_name, [
                f'sky exec {name}-1 -n job-3 \'echo "hello" && exit 1\' || [ $? -eq 100 ]',
                f'sky launch -y -c {name}-1 -n job-4 \'ls file || exit 1\'',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-3 | grep FAILED | awk \'{{print $1}}\' | grep 3',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-4 | grep SUCCEEDED | awk \'{{print $1}}\' | grep 4',
                f's=$(sky queue {name}-1) && echo "$s" && echo "$s" | grep job-1 && exit 1 || true',
                f's=$(sky queue {name}-1 -u) && echo "$s" && echo "$s" | grep {user_2_name} | grep job-3 | grep FAILED',
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
@pytest.mark.no_shadeform  # Shadeform does not support multi-tenant jobs
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
                    timeout=600
                    if smoke_tests_utils.is_remote_server_test() else 60),
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
                        timeout=600
                        if smoke_tests_utils.is_remote_server_test() else 60),
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
# We mark this test as no_remote_server since it requires a dedicated API server
# for the test otherwise we can't make any guarantees about the most recent
# request. Replace with another option to skip shared server tests when we have
# one.
@pytest.mark.no_remote_server
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
    # TODO(kevin): Re-enable this once we have a standardized way to expose /metrics
    # on the non-docker remote API servers used for smoke tests.
    if not smoke_tests_utils.is_docker_remote_api_server():
        pytest.skip(
            'This test is only for remote server setup with setup_docker_container fixture'
        )

    def compare_rss_metrics(baseline: Dict[Tuple[str, ...], List[Tuple[float,
                                                                       float]]],
                            actual: Dict[Tuple[str, ...], List[Tuple[float,
                                                                     float]]]):

        def _rss_per_key_threshold_checker(
                diff: metrics_utils.PerKeyDiff) -> List[str]:
            """Per-key threshold checker for RSS metrics."""
            failures = []
            if diff.actual > 300:
                failures.append(f"exceeded 300 MB: {actual:.1f} MB")
            if diff.increase_pct > 50 and diff.increase_pct != float('inf'):
                failures.append(
                    f"increased by {diff.increase_pct:.1f}% (limit: 50%)")
            return failures

        def _rss_aggregate_threshold_checker(
                diff: metrics_utils.AggregateDiff) -> List[str]:
            """Aggregate threshold checker for RSS metrics."""
            failures = []
            if diff.total_increase_pct > 30:
                failures.append(
                    f"Average memory increase too high: {diff.total_increase_pct:.1f}% (limit: 30%)"
                )
            return failures

        metrics_utils.compare_metrics(
            baseline,
            actual,
            aggregator_fn=metrics_utils.rss_peak_aggregator,
            per_key_checker=_rss_per_key_threshold_checker,
            aggregate_checker=_rss_aggregate_threshold_checker)

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
        'test_api_server_start_stop',
        [
            # To avoid interference with other tests, we launch a separate API server for this test.
            f'sky launch -n {name} --cloud {generic_cloud} tests/test_yamls/apiserver-start-stop.yaml -y {smoke_tests_utils.LOW_RESOURCE_ARG}'
        ],
        f'sky down -y {name} || true',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes  # We only run this test on Kubernetes to test its ssh.
@pytest.mark.no_remote_server  # All blocking testing has been done for local server.
@pytest.mark.no_dependency  # We can't restart the api server in the dependency test.
def test_tail_jobs_logs_blocks_ssh(generic_cloud: str):
    """Test that we don't block ssh when we do a large amount
    of tail logs requests.
    """
    if not smoke_tests_utils.is_in_buildkite_env():
        pytest.skip(
            'Skipping test: requires restarting API server, run only in '
            'Buildkite.')

    name = smoke_tests_utils.get_cluster_name()
    job_name = name + '-job'
    timeout = smoke_tests_utils.get_timeout(generic_cloud)

    num_cpus = os.cpu_count()
    # Use the asyncio logic to get the number of threads that the executor uses
    # so that we can attempt to block them.
    num_threads = min(num_cpus + 4, 32)

    threads = [None for _ in range(num_threads)]
    try:
        # Stop and start the api server to start an server without deployment mode,
        # so that there is only one server process that can be easier to block.
        cmd_one = subprocess.Popen(['sky api stop'], shell=True)
        cmd_one.wait(timeout=timeout)
        cmd_two = subprocess.Popen(['sky api start'], shell=True)
        cmd_two.wait(timeout=timeout)
        # Launch cluster, use the command line because the sdk doesn't
        # lead to websocket_proxy.
        print("Launching cluster...")
        subprocess.Popen([
            'sky launch -c ' + name + ' --infra ' + generic_cloud + ' ' +
            smoke_tests_utils.LOW_RESOURCE_ARG + ' -y'
        ],
                         shell=True)
        print("Cluster launched.")

        # Launch a problematic job with infinite logs.
        task = sky.Task(
            run=
            'for i in {{1..10000}}; do echo "Hello, world! $i"; sleep 1; done')
        task.set_resources(
            sky.Resources(infra=generic_cloud,
                          **smoke_tests_utils.LOW_RESOURCE_PARAM))
        req_id = jobs.launch(task, name=job_name)
        job_ids, _ = sky.stream_and_get(req_id)
        assert len(job_ids) == 1
        job_id = job_ids[0]

        # Wait for the job to start.
        def is_job_started(job_id: int):
            req_id = jobs.queue_v2(refresh=True, job_ids=[job_id])
            job_records = sky.stream_and_get(req_id)[0]
            assert len(job_records) == 1
            return job_records[0]['status'] == sky.ManagedJobStatus.RUNNING

        start_time = time.time()
        while not is_job_started(job_id):
            if time.time() - start_time > timeout:
                raise Exception("Job failed to start.")
            time.sleep(1)
        print("Job started.")

        def log_thread(worker_id: int):
            try:
                jobs.tail_logs(job_id=job_id, follow=True)
            except Exception as e:
                print(f"Error in log thread {worker_id}: {e}")

        # Tail job logs.
        print(
            f"Launching {num_threads} tail log requests to block the workers.")
        for worker_id in range(num_threads):
            threads[worker_id] = threading.Thread(target=log_thread,
                                                  args=(worker_id,))
            threads[worker_id].start()
        print("Requests launched.")

        # Give time for requests to start.
        time.sleep(10)

        print("Attempting to ssh in.")

        # Now attempt to ssh in.
        ssh_cmd = f'ssh -o ConnectTimeout=30 -o BatchMode=yes {name} "echo hi"'
        ssh_ret = subprocess.Popen(ssh_cmd, shell=True)
        if ssh_ret.wait(timeout=60) != 0:
            raise Exception("SSH failed.")

        print("SSH completed.")
    finally:
        # Stop and start the api server to unblock it.
        cmd_one = subprocess.Popen(['sky api stop'], shell=True)
        cmd_one.wait(timeout=timeout)
        cmd_two = subprocess.Popen(['sky api start'], shell=True)
        cmd_two.wait(timeout=timeout)
        # Tear down cluster.
        try:
            print("Tearing down cluster...")
            req_id = sky.down(name)
            sky.stream_and_get(req_id)
            print("Cluster torn down.")
        except Exception:
            pass

        # Cancel job.
        try:
            print(f"Cancelling job {job_id}...")
            req_id = jobs.cancel(job_ids=[job_id])
            sky.stream_and_get(req_id)
            print("Job cancelled.")
        except Exception:
            pass

        # Join threads.
        for thread in threads:
            if thread:
                thread.join()


# TODO(aylei): support running this test on remote server.
# The test infra will use a dedicated API server for each case when remote server is not used.
# TODO(aylei): this assumption does not hold when running this test locally, should figure
# a better way to isolate this test.
@pytest.mark.no_remote_server
@pytest.mark.no_dependency  # We can't restart the api server in the dependency test.
def test_high_logs_concurrency_not_blocking_operations(generic_cloud: str,
                                                       tmp_path: pathlib.Path):
    """Test that high logs concurrency does not block other operations."""
    name = smoke_tests_utils.get_cluster_name()

    tail_log_threads: List[threading.Thread] = []

    def tail_log_thread(idx: int):
        try:
            context.initialize()
            ctx = context.get()
            log_file = tmp_path / f'log_{idx}.txt'
            log_file.touch()
            origin = ctx.redirect_log(log_file)
            sky.tail_logs(cluster_name=name, job_id=None, follow=True)
            ctx.redirect_log(origin)
        except Exception as e:  # pylint: disable=broad-except
            print(f'Error in tail log thread {idx}: {e}')

    def start_tail_logs() -> Generator[str, None, None]:
        yield f'Starting tail log threads, log path: {tmp_path}'
        # Probe we can start log tail first to fail fast on exceptional case
        sky.tail_logs(cluster_name=name, job_id=None, follow=False, tail=10)
        # We expect a single server process will still be responsive even the logs requests
        # rate exceeds its capability.
        # Note that we have to use SDK here to avoid the overhead of too much CLI processes
        # which makes the test flaky.
        tmp_path.mkdir(exist_ok=True)
        for i in range(196):
            thread = threading.Thread(target=tail_log_thread,
                                      args=(i,),
                                      daemon=True)
            tail_log_threads.append(thread)
            thread.start()

    def expect_enough_concurrent_logs() -> Generator[str, None, None]:
        start = time.time()
        # Expect the API server support enough concurrent logs requests
        # within a reasonable time.
        expected_count = 128
        while time.time() - start < 120:
            count = 0
            # Retry on connection errors since the server might be temporarily overwhelmed
            max_retries = 3
            for retry in range(max_retries):
                try:
                    for req in sky.api_status(limit=None):
                        if 'logs' in req.name and req.status == 'RUNNING':
                            count += 1
                    break  # Success, exit retry loop
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.RequestException) as e:
                    if retry == max_retries - 1:
                        raise  # Re-raise on final retry
                    time.sleep(5)  # 5 second backoff before retry
            if count >= expected_count:
                return
            yield f'Wait enough concurrent logs requests: {count}/{expected_count}'
            time.sleep(5)
        raise Exception('Enough concurrent logs requests are not supported')

    test = smoke_tests_utils.Test(
        'test_high_logs_concurrency_not_blocking_operations',
        [
            # Stop and start the api server in non-deployment mode, so that there is only
            # one server process that can be easier to block.
            'sky api stop; sky api start',
            f'sky launch -c {name} --cloud {generic_cloud} \'for i in {{1..102400}}; do echo "Repeat $i"; sleep 1; done\' -y {smoke_tests_utils.LOW_RESOURCE_ARG} --async',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            start_tail_logs,
            expect_enough_concurrent_logs,
            f'sky launch -c {name}-another --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --async',
            f'sky jobs launch -n {name}-job "echo hello" --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --async',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name + '-another',
                cluster_status=[sky.ClusterStatus.UP],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-job',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            # Cancel all requests.
            'sky api cancel -yu',
            # print all non-completed requests for debugging
            'sky api status',
            f'sky down -y {name}',
            f'sky down -y {name}-another',
        ],
        (f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api stop && '
         f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api start; '
         f'sky down -y {name} || true; sky down -y {name}-another || true; '
         f'sky jobs cancel -n {name}-job -y || true;'),
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # Requires restarting the API server to remove existing tunnels.
@pytest.mark.no_dependency  # We can't restart the api server in the dependency test.
def test_high_concurrency_ssh_tunnel_opening(generic_cloud: str,
                                             tmp_path: pathlib.Path):
    """Test that high concurrency SSH tunnel opening does not result in timeouts."""
    name = smoke_tests_utils.get_cluster_name()
    concurrency = 50
    log_file = tmp_path / 'all_logs.txt'
    log_file.touch()

    tail_log_threads: List[threading.Thread] = []
    errors: List[str] = []

    def tail_log_thread(idx: int):
        try:
            context.initialize()
            os.environ = context.ContextualEnviron(os.environ)
            ctx = context.get()
            ctx.override_envs({'SKYPILOT_DEBUG': '1'})
            origin = ctx.redirect_log(log_file)
            sky.tail_logs(cluster_name=name, job_id=None, follow=False)
            ctx.redirect_log(origin)
        except Exception as e:  # pylint: disable=broad-except
            errors.append(f'Error in tail log thread {idx}: {e}')

    def start_concurrent_tail_logs() -> Generator[str, None, None]:
        start_time = time.time()
        for i in range(concurrency):
            thread = threading.Thread(target=tail_log_thread,
                                      args=(i,),
                                      daemon=True)
            tail_log_threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in tail_log_threads:
            thread.join(timeout=60)

        elapsed = time.time() - start_time
        yield f'All {len(tail_log_threads)} concurrent tail_logs completed in {elapsed:.2f}s'

        if errors:
            raise Exception(f'Errors in tail log threads: {errors}')

    test = smoke_tests_utils.Test(
        'test_concurrent_tunnel_opening',
        [
            f'sky launch -c {name} --infra {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.UP],
                timeout=180),
            # Restart the API server to remove existing tunnels.
            'sky api stop; sky api start',
            start_concurrent_tail_logs,
            # Print the full logs for debugging.
            f'echo "=== FULL LOGS ===" && cat {log_file}',
            f'echo "=== ERRORS ===" && ! grep "sky.utils.locks.LockTimeout" {log_file} && echo "No LockTimeout errors"',
            # Verify that all the tail logs requests succeeded.
            # Assume the API server is isolated for this test only.
            f's=$(sky api status -a -l all | grep "sky.logs" | grep SUCCEEDED) && echo $s && echo "$s" | wc -l | grep {concurrency}',
        ],
        (f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api stop && '
         f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api start; '
         f'sky down -y {name}'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test WebSocket large cookie header ----------
@pytest.mark.no_remote_server
def test_websocket_large_cookie_accepted():
    """Test that WebSocket connections with large cookies (>8KB) are accepted.

    Enterprise SSO cookies from oauth2proxy (Azure AD, Okta, etc.) can exceed
    8KB, which hits the websockets library's default MAX_LINE_LENGTH=8192 limit
    and causes WebSocket upgrade requests to be rejected with HTTP 400. Regular
    HTTP requests (parsed by h11 with a 16KB default) are unaffected, which is
    why sky status works but sky ssh fails.

    The server must increase the websockets library limit so that WebSocket
    upgrade requests with large cookie headers are not rejected at the protocol
    parsing layer.
    """
    server_url = server_common.get_server_url()
    ws_url = server_url.replace('http://',
                                'ws://').replace('https://', 'wss://')
    ws_url += '/kubernetes-pod-ssh-proxy'

    async def _test():
        # 12KB cookie simulating an enterprise SSO session cookie.
        pad = 'X' * 12000
        try:
            ws = await websockets.connect(
                ws_url,
                additional_headers={'Cookie': f'_oauth2_proxy={pad}'},
                open_timeout=5)
            await ws.close()
        except websockets.exceptions.InvalidStatus as e:
            # HTTP 403 = auth rejection (expected, no valid auth token)
            # HTTP 400 = header parsing failure (the bug)
            assert e.response.status_code != 400, (
                'WebSocket rejected with HTTP 400 due to large cookie '
                'header. The websockets library MAX_LINE_LENGTH is too '
                'small for enterprise SSO cookies (>8KB).')

    asyncio.run(_test())
