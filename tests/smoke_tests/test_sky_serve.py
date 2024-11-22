# Smoke tests for SkyPilot for sky serve
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_sky_serve.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_sky_serve.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_sky_serve.py::test_skyserve_gcp_http
#
# Only run sky serve tests
# > pytest tests/smoke_tests/test_sky_server.py --sky-serve
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_sky_serve.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_sky_serve.py --generic-cloud aws

import inspect
import json
import shlex
from typing import List, Tuple

import pytest
from smoke_tests.util import get_cluster_name
from smoke_tests.util import run_one_test
from smoke_tests.util import terminate_gcp_replica
from smoke_tests.util import Test
from smoke_tests.util import test_id

from sky import serve
from sky.utils import common_utils

# ---------- Testing skyserve ----------


def _get_service_name() -> str:
    """Returns a user-unique service name for each test_skyserve_<name>().

    Must be called from each test_skyserve_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    test_name = test_name.replace('skyserve-', 'ss-')
    test_name = common_utils.make_cluster_name_on_cloud(test_name, 24)
    return f'{test_name}-{test_id}'


# We check the output of the skyserve service to see if it is ready. Output of
# `REPLICAS` is in the form of `1/2` where the first number is the number of
# ready replicas and the second number is the number of total replicas. We
# grep such format to ensure that the service is ready, and early exit if any
# failure detected. In the end we sleep for
# serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS to make sure load balancer have
# enough time to sync with the controller and get all ready replica IPs.
_SERVE_WAIT_UNTIL_READY = (
    '{{ while true; do'
    '     s=$(sky serve status {name}); echo "$s";'
    '     echo "$s" | grep -q "{replica_num}/{replica_num}" && break;'
    '     echo "$s" | grep -q "FAILED" && exit 1;'
    '     sleep 10;'
    ' done; }}; echo "Got service status $s";'
    f'sleep {serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS + 2};')
_IP_REGEX = r'([0-9]{1,3}\.){3}[0-9]{1,3}'
_AWK_ALL_LINES_BELOW_REPLICAS = r'/Replicas/{flag=1; next} flag'
_SERVICE_LAUNCHING_STATUS_REGEX = 'PROVISIONING\|STARTING'
# Since we don't allow terminate the service if the controller is INIT,
# which is common for simultaneous pytest, we need to wait until the
# controller is UP before we can terminate the service.
# The teardown command has a 10-mins timeout, so we don't need to do
# the timeout here. See implementation of run_one_test() for details.
_TEARDOWN_SERVICE = (
    '(for i in `seq 1 20`; do'
    '     s=$(sky serve down -y {name});'
    '     echo "Trying to terminate {name}";'
    '     echo "$s";'
    '     echo "$s" | grep -q "scheduled to be terminated\|No service to terminate" && break;'
    '     sleep 10;'
    '     [ $i -eq 20 ] && echo "Failed to terminate service {name}";'
    'done)')

_SERVE_ENDPOINT_WAIT = (
    'export ORIGIN_SKYPILOT_DEBUG=$SKYPILOT_DEBUG; export SKYPILOT_DEBUG=0; '
    'endpoint=$(sky serve status --endpoint {name}); '
    'until ! echo "$endpoint" | grep "Controller is initializing"; '
    'do echo "Waiting for serve endpoint to be ready..."; '
    'sleep 5; endpoint=$(sky serve status --endpoint {name}); done; '
    'export SKYPILOT_DEBUG=$ORIGIN_SKYPILOT_DEBUG; echo "$endpoint"')

_SERVE_STATUS_WAIT = ('s=$(sky serve status {name}); '
                      'until ! echo "$s" | grep "Controller is initializing."; '
                      'do echo "Waiting for serve status to be ready..."; '
                      'sleep 5; s=$(sky serve status {name}); done; echo "$s"')


def _get_replica_ip(name: str, replica_id: int) -> str:
    return (f'ip{replica_id}=$(echo "$s" | '
            f'awk "{_AWK_ALL_LINES_BELOW_REPLICAS}" | '
            f'grep -E "{name}\s+{replica_id}" | '
            f'grep -Eo "{_IP_REGEX}")')


def _get_skyserve_http_test(name: str, cloud: str,
                            timeout_minutes: int) -> Test:
    test = Test(
        f'test-skyserve-{cloud.replace("_", "-")}',
        [
            f'sky serve up -n {name} -y tests/skyserve/http/{cloud}.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=timeout_minutes * 60,
    )
    return test


def _check_replica_in_status(name: str, check_tuples: List[Tuple[int, bool,
                                                                 str]]) -> str:
    """Check replicas' status and count in sky serve status

    We will check vCPU=2, as all our tests use vCPU=2.

    Args:
        name: the name of the service
        check_tuples: A list of replica property to check. Each tuple is
            (count, is_spot, status)
    """
    check_cmd = ''
    for check_tuple in check_tuples:
        count, is_spot, status = check_tuple
        resource_str = ''
        if status not in ['PENDING', 'SHUTTING_DOWN'
                         ] and not status.startswith('FAILED'):
            spot_str = ''
            if is_spot:
                spot_str = '\[Spot\]'
            resource_str = f'({spot_str}vCPU=2)'
        check_cmd += (f' echo "$s" | grep "{resource_str}" | '
                      f'grep "{status}" | wc -l | grep {count} || exit 1;')
    return (f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s"; ' + check_cmd)


def _check_service_version(service_name: str, version: str) -> str:
    # Grep the lines before 'Service Replicas' and check if the service version
    # is correct.
    return (f'echo "$s" | grep -B1000 "Service Replicas" | '
            f'grep -E "{service_name}\s+{version}" || exit 1; ')


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_gcp_http():
    """Test skyserve on GCP"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'gcp', 20)
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.serve
def test_skyserve_aws_http():
    """Test skyserve on AWS"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'aws', 20)
    run_one_test(test)


@pytest.mark.azure
@pytest.mark.serve
def test_skyserve_azure_http():
    """Test skyserve on Azure"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'azure', 30)
    run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.serve
def test_skyserve_kubernetes_http():
    """Test skyserve on Kubernetes"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'kubernetes', 30)
    run_one_test(test)


@pytest.mark.oci
@pytest.mark.serve
def test_skyserve_oci_http():
    """Test skyserve on OCI"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'oci', 20)
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support T4 gpus for now
@pytest.mark.serve
def test_skyserve_llm(generic_cloud: str):
    """Test skyserve with real LLM usecase"""
    name = _get_service_name()

    def generate_llm_test_command(prompt: str, expected_output: str) -> str:
        prompt = shlex.quote(prompt)
        expected_output = shlex.quote(expected_output)
        return (
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'python tests/skyserve/llm/get_response.py --endpoint $endpoint '
            f'--prompt {prompt} | grep {expected_output}')

    with open('tests/skyserve/llm/prompt_output.json', 'r',
              encoding='utf-8') as f:
        prompt2output = json.load(f)

    test = Test(
        f'test-skyserve-llm',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/llm/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            *[
                generate_llm_test_command(prompt, output)
                for prompt, output in prompt2output.items()
            ],
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=40 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_spot_recovery():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = Test(
        f'test-skyserve-spot-recovery-gcp',
        [
            f'sky serve up -n {name} -y tests/skyserve/spot/recovery.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            terminate_gcp_replica(name, zone, 1),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.serve
@pytest.mark.no_kubernetes
def test_skyserve_base_ondemand_fallback(generic_cloud: str):
    name = _get_service_name()
    test = Test(
        f'test-skyserve-base-ondemand-fallback',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/spot/base_ondemand_fallback.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(1, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_dynamic_ondemand_fallback():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = Test(
        f'test-skyserve-dynamic-ondemand-fallback',
        [
            f'sky serve up -n {name} --cloud gcp -y tests/skyserve/spot/dynamic_ondemand_fallback.yaml',
            f'sleep 40',
            # 2 on-demand (provisioning) + 2 Spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s";'
            'echo "$s" | grep -q "0/4" || exit 1',
            # Wait for the provisioning starts
            f'sleep 40',
            _check_replica_in_status(name, [
                (2, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                (2, False, _SERVICE_LAUNCHING_STATUS_REGEX + '\|SHUTTING_DOWN')
            ]),

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
            terminate_gcp_replica(name, zone, 1),
            f'sleep 40',
            # 1 on-demand (provisioning) + 1 Spot (ready) + 1 spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'echo "$s" | grep -q "1/3"',
            _check_replica_in_status(
                name, [(1, True, 'READY'),
                       (1, True, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]),

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.serve
def test_skyserve_user_bug_restart(generic_cloud: str):
    """Tests that we restart the service after user bug."""
    # TODO(zhwu): this behavior needs some rethinking.
    name = _get_service_name()
    test = Test(
        f'test-skyserve-user-bug-restart',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/restart/user_bug.yaml',
            f's=$(sky serve status {name}); echo "$s";'
            'until echo "$s" | grep -A 100 "Service Replicas" | grep "SHUTTING_DOWN"; '
            'do echo "Waiting for first service to be SHUTTING DOWN..."; '
            f'sleep 5; s=$(sky serve status {name}); echo "$s"; done; ',
            f's=$(sky serve status {name}); echo "$s";'
            'until echo "$s" | grep -A 100 "Service Replicas" | grep "FAILED"; '
            'do echo "Waiting for first service to be FAILED..."; '
            f'sleep 5; s=$(sky serve status {name}); echo "$s"; done; echo "$s"; '
            + _check_replica_in_status(name, [(1, True, 'FAILED')]) +
            # User bug failure will cause no further scaling.
            f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 1; '
            f'echo "$s" | grep -B 100 "NO_REPLICA" | grep "0/0"',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/auto_restart.yaml',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, SkyPilot here!"; do sleep 2; done; sleep 2; '
            + _check_replica_in_status(name, [(1, False, 'READY'),
                                              (1, False, 'FAILED')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
@pytest.mark.no_kubernetes  # Replicas on k8s may be running on the same node and have the same public IP
def test_skyserve_load_balancer(generic_cloud: str):
    """Test skyserve load balancer round-robin policy"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-load-balancer',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/load_balancer/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            f'{_get_replica_ip(name, 1)}; '
            f'{_get_replica_ip(name, 2)}; {_get_replica_ip(name, 3)}; '
            'python tests/skyserve/load_balancer/test_round_robin.py '
            '--endpoint $endpoint --replica-num 3 --replica-ips $ip1 $ip2 $ip3',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
@pytest.mark.no_kubernetes
def test_skyserve_auto_restart():
    """Test skyserve with auto restart"""
    name = _get_service_name()
    zone = 'us-central1-a'
    test = Test(
        f'test-skyserve-auto-restart',
        [
            # TODO(tian): we can dynamically generate YAML from template to
            # avoid maintaining too many YAML files
            f'sky serve up -n {name} -y tests/skyserve/auto_restart.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            # sleep for 20 seconds (initial delay) to make sure it will
            # be restarted
            f'sleep 20',
            terminate_gcp_replica(name, zone, 1),
            # Wait for consecutive failure timeout passed.
            # If the cluster is not using spot, it won't check the cluster status
            # on the cloud (since manual shutdown is not a common behavior and such
            # queries takes a lot of time). Instead, we think continuous 3 min probe
            # failure is not a temporary problem but indeed a failure.
            'sleep 180',
            # We cannot use _SERVE_WAIT_UNTIL_READY; there will be a intermediate time
            # that the output of `sky serve status` shows FAILED and this status will
            # cause _SERVE_WAIT_UNTIL_READY to early quit.
            '(while true; do'
            f'    output=$(sky serve status {name});'
            '     echo "$output" | grep -q "1/1" && break;'
            '     sleep 10;'
            f'done); sleep {serve.LB_CONTROLLER_SYNC_INTERVAL_SECONDS};',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_cancel(generic_cloud: str):
    """Test skyserve with cancel"""
    name = _get_service_name()

    test = Test(
        f'test-skyserve-cancel',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/cancel/cancel.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; python3 '
            'tests/skyserve/cancel/send_cancel_request.py '
            '--endpoint $endpoint | grep "Request was cancelled"',
            f's=$(sky serve logs {name} 1 --no-follow); '
            'until ! echo "$s" | grep "Please wait for the controller to be"; '
            'do echo "Waiting for serve logs"; sleep 10; '
            f's=$(sky serve logs {name} 1 --no-follow); done; '
            'echo "$s"; echo "$s" | grep "Client disconnected, stopping computation"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_streaming(generic_cloud: str):
    """Test skyserve with streaming"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-streaming',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/streaming/streaming.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'python3 tests/skyserve/streaming/send_streaming_request.py '
            '--endpoint $endpoint | grep "Streaming test passed"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_readiness_timeout_fail(generic_cloud: str):
    """Test skyserve with large readiness probe latency, expected to fail"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-readiness-timeout-fail',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/readiness_timeout/task.yaml',
            # None of the readiness probe will pass, so the service will be
            # terminated after the initial delay.
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_INITIAL_DELAY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            'sleep 60',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s" | grep "{name}" | grep "FAILED_INITIAL_DELAY" | wc -l | grep 1;'
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_large_readiness_timeout(generic_cloud: str):
    """Test skyserve with customized large readiness timeout"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-large-readiness-timeout',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/readiness_timeout/task_large_timeout.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl http://$endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.serve
def test_skyserve_update(generic_cloud: str):
    """Test skyserve with update"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-update',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/old.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/new.yaml',
            # sleep before update is registered.
            'sleep 20',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done;'
            # Make sure the traffic is not mixed
            'curl http://$endpoint | grep "Hi, new SkyPilot here"',
            # The latest 2 version should be READY and the older versions should be shutting down
            (_check_replica_in_status(name, [(2, False, 'READY'),
                                             (2, False, 'SHUTTING_DOWN')]) +
             _check_service_version(name, "2")),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.serve
def test_skyserve_rolling_update(generic_cloud: str):
    """Test skyserve with rolling update"""
    name = _get_service_name()
    single_new_replica = _check_replica_in_status(
        name, [(2, False, 'READY'), (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
               (1, False, 'SHUTTING_DOWN')])
    test = Test(
        f'test-skyserve-rolling-update',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/old.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/new.yaml',
            # Make sure the traffic is mixed across two versions, the replicas
            # with even id will sleep 60 seconds before being ready, so we
            # should be able to get observe the period that the traffic is mixed
            # across two versions.
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl http://$endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done; sleep 2; '
            # The latest version should have one READY and the one of the older versions should be shutting down
            f'{single_new_replica} {_check_service_version(name, "1,2")} '
            # Check the output from the old version, immediately after the
            # output from the new version appears. This is guaranteed by the
            # round robin load balancing policy.
            # TODO(zhwu): we should have a more generalized way for checking the
            # mixed version of replicas to avoid depending on the specific
            # round robin load balancing policy.
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack
@pytest.mark.serve
def test_skyserve_fast_update(generic_cloud: str):
    """Test skyserve with fast update (Increment version of old replicas)"""
    name = _get_service_name()

    test = Test(
        f'test-skyserve-fast-update',
        [
            f'sky serve up -n {name} -y --cloud {generic_cloud} tests/skyserve/update/bump_version_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/bump_version_after.yaml',
            # sleep to wait for update to be registered.
            'sleep 40',
            # 2 on-deamnd (ready) + 1 on-demand (provisioning).
            (
                _check_replica_in_status(
                    name, [(2, False, 'READY'),
                           (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]) +
                # Fast update will directly have the latest version ready.
                _check_service_version(name, "2")),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3) +
            _check_service_version(name, "2"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
            # Test rolling update
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/bump_version_before.yaml',
            # sleep to wait for update to be registered.
            'sleep 25',
            # 2 on-deamnd (ready) + 1 on-demand (shutting down).
            _check_replica_in_status(name, [(2, False, 'READY'),
                                            (1, False, 'SHUTTING_DOWN')]),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "3"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl http://$endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.serve
def test_skyserve_update_autoscale(generic_cloud: str):
    """Test skyserve update with autoscale"""
    name = _get_service_name()
    test = Test(
        f'test-skyserve-update-autoscale',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/num_min_two.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "1"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode blue_green -y tests/skyserve/update/num_min_one.yaml',
            # sleep before update is registered.
            'sleep 20',
            # Timeout will be triggered when update fails.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1) +
            _check_service_version(name, "2"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here!"',
            # Rolling Update
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/update/num_min_two.yaml',
            # sleep before update is registered.
            'sleep 20',
            # Timeout will be triggered when update fails.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "3"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here!"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Spot instances are note supported by Fluidstack
@pytest.mark.serve
@pytest.mark.no_kubernetes  # Spot instances are not supported in Kubernetes
@pytest.mark.parametrize('mode', ['rolling', 'blue_green'])
def test_skyserve_new_autoscaler_update(mode: str, generic_cloud: str):
    """Test skyserve with update that changes autoscaler"""
    name = f'{_get_service_name()}-{mode}'

    wait_until_no_pending = (
        f's=$(sky serve status {name}); echo "$s"; '
        'until ! echo "$s" | grep PENDING; do '
        '  echo "Waiting for replica to be out of pending..."; '
        f' sleep 5; s=$(sky serve status {name}); '
        '  echo "$s"; '
        'done')
    four_spot_up_cmd = _check_replica_in_status(name, [(4, True, 'READY')])
    update_check = [f'until ({four_spot_up_cmd}); do sleep 5; done; sleep 15;']
    if mode == 'rolling':
        # Check rolling update, it will terminate one of the old on-demand
        # instances, once there are 4 spot instance ready.
        update_check += [
            _check_replica_in_status(
                name, [(1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (1, False, 'SHUTTING_DOWN'), (1, False, 'READY')]) +
            _check_service_version(name, "1,2"),
        ]
    else:
        # Check blue green update, it will keep both old on-demand instances
        # running, once there are 4 spot instance ready.
        update_check += [
            _check_replica_in_status(
                name, [(1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (2, False, 'READY')]) +
            _check_service_version(name, "1"),
        ]
    test = Test(
        f'test-skyserve-new-autoscaler-update-{mode}',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/update/new_autoscaler_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "1"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            's=$(curl http://$endpoint); echo "$s"; echo "$s" | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --cloud {generic_cloud} --mode {mode} -y tests/skyserve/update/new_autoscaler_after.yaml',
            # Wait for update to be registered
            f'sleep 90',
            wait_until_no_pending,
            _check_replica_in_status(
                name, [(4, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (2, False, 'READY')]),
            *update_check,
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=5),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl http://$endpoint | grep "Hi, SkyPilot here"',
            _check_replica_in_status(name, [(4, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.serve
def test_skyserve_failures(generic_cloud: str):
    """Test replica failure statuses"""
    name = _get_service_name()

    test = Test(
        'test-skyserve-failures',
        [
            f'sky serve up -n {name} --cloud {generic_cloud} -y tests/skyserve/failures/initial_delay.yaml',
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_INITIAL_DELAY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            'sleep 60',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s" | grep "{name}" | grep "FAILED_INITIAL_DELAY" | wc -l | grep 2; '
            # Make sure no new replicas are started for early failure.
            f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 2;',
            f'sky serve update {name} --cloud {generic_cloud} -y tests/skyserve/failures/probing.yaml',
            f's=$(sky serve status {name}); '
            # Wait for replica to be ready.
            f'until echo "$s" | grep "READY"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done;',
            # Wait for replica to change to FAILED_PROBING
            f's=$(sky serve status {name}); '
            f'until echo "$s" | grep "FAILED_PROBING"; do '
            'echo "Waiting for replica to be failed..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done',
            # Wait for the PENDING replica to appear.
            'sleep 10',
            # Wait until the replica is out of PENDING.
            f's=$(sky serve status {name}); '
            f'until ! echo "$s" | grep "PENDING" && ! echo "$s" | grep "Please wait for the controller to be ready."; do '
            'echo "Waiting for replica to be out of pending..."; sleep 5; '
            f's=$(sky serve status {name}); echo "$s"; done; ' +
            _check_replica_in_status(
                name, [(1, False, 'FAILED_PROBING'),
                       (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]),
            # TODO(zhwu): add test for FAILED_PROVISION
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
    )
    run_one_test(test)


# TODO(Ziming, Tian): Add tests for autoscaling.


# ------- Testing user dependencies --------
def test_user_dependencies(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'user-dependencies',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} "pip install ray>2.11; ray start --head"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} "echo hi"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
            f'sky exec {name} "echo bye"',
            f'sky logs {name} 3 --status',
            f'sky launch -c {name} tests/test_yamls/different_default_conda_env.yaml',
            f'sky logs {name} 4 --status',
            # Launch again to test the default env does not affect SkyPilot
            # runtime setup
            f'sky launch -c {name} "python --version 2>&1 | grep \'Python 3.6\' || exit 1"',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)
