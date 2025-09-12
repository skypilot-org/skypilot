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
import os
import shlex
import tempfile
from typing import Dict, List, Tuple

import pytest
from smoke_tests import smoke_tests_utils

from sky import serve
from sky import skypilot_config
from sky.utils import common_utils
from sky.utils import subprocess_utils

# ---------- Testing skyserve ----------


def _get_service_name() -> str:
    """Returns a user-unique service name for each test_skyserve_<name>().

    Must be called from each test_skyserve_<name>().
    """
    caller_func_name = inspect.stack()[1][3]
    test_name = caller_func_name.replace('_', '-').replace('test-', 't-')
    test_name = test_name.replace('skyserve-', 'ss-')
    test_name = common_utils.make_cluster_name_on_cloud(test_name, 24)
    return f'{test_name}-{smoke_tests_utils.test_id}'


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
_AWK_ALL_LINES_BELOW_REPLICAS = '/Replicas/{flag=1; next} flag'
_SERVICE_LAUNCHING_STATUS_REGEX = r'PROVISIONING\|STARTING'

_SHOW_SERVE_STATUS = (
    'echo "+ sky serve status {name}"; '
    'sky serve status {name}; '
    'echo "+ sky serve logs --controller {name} --no-follow"; '
    'sky serve logs --controller {name} --no-follow; '
    'echo "+ sky serve logs --load-balancer {name} --no-follow"; '
    'sky serve logs --load-balancer {name} --no-follow; ')

# Since we don't allow terminate the service if the controller is INIT,
# which is common for simultaneous pytest, we need to wait until the
# controller is UP before we can terminate the service.
# The teardown command has a 10-mins timeout, so we don't need to do
# the timeout here. See implementation of run_one_test() for details.
_TEARDOWN_SERVICE = _SHOW_SERVE_STATUS + (
    '(for i in `seq 1 20`; do'
    '     s=$(sky serve down -y {name});'
    '     echo "Trying to terminate {name}";'
    '     echo "$s";'
    r'     echo "$s" | grep -q "scheduled to be terminated\|No service to terminate" && break;'
    '     sleep 10;'
    '     [ $i -eq 20 ] && echo "Failed to terminate service {name}";'
    'done; '
    # Wait for service to be fully terminated
    'start_time=$(date +%s); '
    'timeout=600; '
    'while true; do '
    '    status_output=$(sky serve status {name} 2>&1); '
    '    echo "Checking service termination status..."; '
    '    echo "$status_output"; '
    '    echo "$status_output" | grep -q "Service \'{name}\' not found" && echo "Service terminated successfully" && exit 0; '
    '    current_time=$(date +%s); '
    '    elapsed=$((current_time - start_time)); '
    '    if [ "$elapsed" -ge "$timeout" ]; then '
    '        echo "Timeout: Service {name} not terminated within 5 minutes"; '
    '        exit 1; '
    '    fi; '
    '    echo "Service still terminating, waiting 10 seconds..."; '
    '    sleep 10; '
    'done)')

_SERVE_ENDPOINT_WAIT = (
    'export ORIGIN_SKYPILOT_DEBUG=$SKYPILOT_DEBUG; export SKYPILOT_DEBUG=0; '
    'endpoint=$(sky serve status --endpoint {name}); '
    'until ! echo "$endpoint" | grep -qE "Controller is initializing|^-$"; '
    'do echo "Waiting for serve endpoint to be ready..."; '
    'sleep 5; endpoint=$(sky serve status --endpoint {name}); done; '
    'export SKYPILOT_DEBUG=$ORIGIN_SKYPILOT_DEBUG; echo "$endpoint"')

_SERVE_STATUS_WAIT = (
    's=$(sky serve status {name}); '
    # Wait for "Controller is initializing." to disappear
    'until ! echo "$s" | grep "Controller is initializing."; '
    'do '
    '    echo "Waiting for serve status to be ready..."; '
    '    sleep 5; '
    '    s=$(sky serve status {name}); '
    'done; '
    'echo "$s"')

_WAIT_PROVISION_REPR = (
    # Once controller is ready, check provisioning vs. cpus=2. This is for
    # the `_check_replica_in_status`, which will check number of `cpus=2` in the
    # `sky serve status` output and use that to suggest the number of replicas.
    # However, replicas in provisioning state is possible to have a repr of `-`,
    # since the desired `launched_resources` is not decided yet. This would
    # cause an error when counting desired number of replicas. We wait for the
    # representation of `cpus=2` the same with number of provisioning replicas
    # to avoid this error.
    # NOTE(tian): This assumes the replica will not do failover, as the
    # requested resources is only 2 vCPU and likely to be immediately available
    # on every region, hence no failover. If the replica will go through
    # failover
    # Check #4565 for more information.
    'num_provisioning=$(echo "$s" | grep "PROVISIONING" | wc -l); '
    'num_vcpu_in_provision=$(echo "$s" | grep "PROVISIONING" | grep "x(cpus=2, " | wc -l); '
    'until [ "$num_provisioning" -eq "$num_vcpu_in_provision" ]; '
    'do '
    '    echo "Waiting for provisioning resource repr ready..."; '
    '    echo "PROVISIONING: $num_provisioning, vCPU: $num_vcpu_in_provision"; '
    '    sleep 2; '
    '    s=$(sky serve status {name}); '
    '    num_provisioning=$(echo "$s" | grep "PROVISIONING" | wc -l); '
    '    num_vcpu_in_provision=$(echo "$s" | grep "PROVISIONING" | grep "x(cpus=2, " | wc -l); '
    'done; '
    # Provisioning is complete
    'echo "Provisioning complete. PROVISIONING: $num_provisioning, cpus=2: $num_cpus_in_provision"'
)

# Shell script snippet to monitor and wait for resolution of NOT_READY status:
# EKS clusters and services can be slow to initialize, causing both the service and replicas
# to remain in NOT_READY status for extended periods. This script:
# 1. Runs for max 5 minutes
# 2. Specifically checks 10th column (STATUS) for "NOT_READY"
# 3. Ignores other states like SHUTTING_DOWN/STARTING
# 4. Exits successfully when zero NOT_READY services/replicas remain
# 5. Fails immediately if timeout reached
_WAIT_NO_NOT_READY = (
    'start_time=$(date +%s); '
    'timeout=300; '
    'while true; do '
    '    not_ready_count=$(sky serve status {name} | '
    '        awk \'/{name}/ && $10 == "NOT_READY" {{print}}\' | '
    '        wc -l); '
    '    [ "$not_ready_count" -eq 0 ] && break; '
    '    current_time=$(date +%s); '
    '    elapsed=$((current_time - start_time)); '
    '    if [ "$elapsed" -ge "$timeout" ]; then '
    '        echo "Timeout: $not_ready_count service/replica(s) stuck in NOT_READY"; '
    '        exit 1; '
    '    fi; '
    '    echo "Waiting for $not_ready_count NOT_READY service/replicas..."; '
    '    sleep 10; '
    'done')


def _get_replica_ip(name: str, replica_id: int) -> str:
    return (f'ip{replica_id}=$(echo "$s" | '
            f'awk "{_AWK_ALL_LINES_BELOW_REPLICAS}" | '
            f'grep -E "{name}\s+{replica_id}" | '
            f'grep -Eo "{_IP_REGEX}")')


def _get_skyserve_http_test(name: str, cloud: str,
                            timeout_minutes: int) -> smoke_tests_utils.Test:
    test = smoke_tests_utils.Test(
        f'test-skyserve-{cloud.replace("_", "-")}',
        [
            f'sky serve up -n {name} -y {smoke_tests_utils.LOW_RESOURCE_ARG} tests/skyserve/http/{cloud}.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=timeout_minutes * 60,
    )
    return test


def _check_replica_in_status(name: str,
                             check_tuples: List[Tuple[int, bool, str]],
                             timeout_seconds: int = 0) -> str:
    """Check replicas' status and count in sky serve status

    We will check cpus=2, as all our tests use cpus=2.

    Args:
        name: the name of the service
        check_tuples: A list of replica property to check. Each tuple is
            (count, is_spot, status)
        timeout_seconds: If greater than 0, wait up to this many seconds for the
            conditions to be met, checking every 5 seconds. If 0, use the original
            logic that fails immediately if conditions aren't met.
    """
    # Build the check conditions
    check_conditions = []
    for check_tuple in check_tuples:
        count, is_spot, status = check_tuple
        resource_str = ''
        if status not in ['PENDING', 'SHUTTING_DOWN'
                         ] and not status.startswith('FAILED'):
            spot_str = ''
            if is_spot:
                spot_str = r'\[spot\]'
            resource_str = f'x{spot_str}(cpus=2, '
        check_conditions.append(
            f'echo "$s" | grep "{resource_str}" | grep "{status}" | wc -l | '
            f'grep {count}')

    if timeout_seconds > 0:
        # Create a timeout mechanism that will wait up to timeout_seconds
        check_cmd = (
            f'start_time=$(date +%s); '
            f'timeout={timeout_seconds}; '  # Use the provided timeout
            f'while true; do '
            f'    s=$(sky serve status {name}); '
            f'    echo "$s"; '
            f'    all_conditions_met=true; ')

        # Add each condition to the check
        for condition in check_conditions:
            check_cmd += f'    {condition} || {{ all_conditions_met=false; }}; '

        # Add the timeout logic
        check_cmd += (
            '    if [ "$all_conditions_met" = true ]; then '
            '        echo "All replica status conditions met"; '
            '        break; '
            '    fi; '
            '    current_time=$(date +%s); '
            '    elapsed=$((current_time - start_time)); '
            '    if [ "$elapsed" -ge "$timeout" ]; then '
            '        echo "Timeout: Replica status conditions not met after '
            '$timeout seconds"; '
            '        exit 1; '
            '    fi; '
            '    echo "Waiting for replica status conditions to be met..."; '
            '    sleep 5; '  # Check every 5 seconds
            'done; ')  # Add semicolon here
    else:
        # Original logic that fails immediately if conditions aren't met
        check_cmd = ''
        for condition in check_conditions:
            check_cmd += f'{condition} || exit 1; '

    return (f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            f'{_WAIT_PROVISION_REPR.format(name=name)}; '
            f'{_WAIT_NO_NOT_READY.format(name=name)}; '
            f'echo "$s"; {check_cmd}')


def _check_service_version(service_name: str, version: str) -> str:
    # Grep the lines before 'Service Replicas' and check if the service version
    # is correct.
    return (f'echo "$s" | grep -B1000 "Service Replicas" | '
            rf'grep -E "{service_name}\s+{version}" || exit 1; ')


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_gcp_http():
    """Test skyserve on GCP"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'gcp', 20)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.serve
def test_skyserve_aws_http():
    """Test skyserve on AWS"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'aws', 20)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
@pytest.mark.serve
def test_skyserve_azure_http():
    """Test skyserve on Azure"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'azure', 30)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_kubernetes_http():
    """Test skyserve on Kubernetes"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'kubernetes', 30)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.oci
@pytest.mark.serve
def test_skyserve_oci_http():
    """Test skyserve on OCI"""
    name = _get_service_name()
    test = _get_skyserve_http_test(name, 'oci', 20)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support T4 gpus for now
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'L40S', 'seeweb': 'L40S'}])
@pytest.mark.serve
@pytest.mark.resource_heavy
def test_skyserve_llm(generic_cloud: str, accelerator: Dict[str, str]):
    """Test skyserve with real LLM usecase"""
    if generic_cloud == 'kubernetes':
        accelerator = smoke_tests_utils.get_avaliabe_gpus_for_k8s_tests()
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')

    name = _get_service_name()
    auth_token = '123456'

    def generate_llm_test_command(prompt: str, expected_output: str) -> str:
        prompt = shlex.quote(prompt)
        expected_output = shlex.quote(expected_output)
        return (
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            's=$(python tests/skyserve/llm/get_response.py --endpoint $endpoint '
            f'--prompt {prompt} --auth_token {auth_token}); '
            'echo "$s"; '
            f'echo "$s" | grep -E {expected_output}')

    with open('tests/skyserve/llm/prompt_output.json', 'r',
              encoding='utf-8') as f:
        prompt2output = json.load(f)

    test = smoke_tests_utils.Test(
        'test-skyserve-llm',
        [
            f'sky serve up -n {name} --infra {generic_cloud} --gpus {accelerator} -y --secret AUTH_TOKEN={auth_token} tests/skyserve/llm/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            *[
                generate_llm_test_command(prompt, output)
                for prompt, output in prompt2output.items()
            ],
        ],
        _TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=40 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_spot_recovery():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = smoke_tests_utils.Test(
        'test-skyserve-spot-recovery-gcp',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky serve up -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/spot/recovery.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl $endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, smoke_tests_utils.terminate_gcp_replica(name, zone, 1)),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl $endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}; '
        f'{_TEARDOWN_SERVICE.format(name=name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.serve
@pytest.mark.no_kubernetes
@pytest.mark.no_do
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
def test_skyserve_base_ondemand_fallback(generic_cloud: str):
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-base-ondemand-fallback',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/spot/base_ondemand_fallback.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(1, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_dynamic_ondemand_fallback():
    name = _get_service_name()
    zone = 'us-central1-a'

    test = smoke_tests_utils.Test(
        'test-skyserve-dynamic-ondemand-fallback',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky serve up -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/spot/dynamic_ondemand_fallback.yaml',
            f'sleep 40',
            # 2 on-demand (provisioning) + 2 Spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s";'
            'echo "$s" | grep -q "0/4" || exit 1',
            # Wait for the provisioning starts
            'sleep 40',
            _check_replica_in_status(name, [
                (2, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
            ]),

            # a) The instance may still be in READY state when we check the
            # status, before transitioning to SHUTTING_DOWN.
            # b) And in SHUTTING_DOWN state, it may actually SHUTDOWN and
            # disappear. So we check 1 instance instead of 2. Because it
            # can be 1 or 2.
            f'count=$(sky serve status {name} | grep "x(cpus=2, " | grep "{_SERVICE_LAUNCHING_STATUS_REGEX}\|SHUTTING_DOWN\|READY" | wc -l); '
            f'[ "$count" -eq 1 ] || [ "$count" -eq 2 ] || {{ echo "Expected 1 or 2 instances, got $count"; exit 1; }}',

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, smoke_tests_utils.terminate_gcp_replica(name, zone, 1)),
            f'sleep 40',
            # 1 on-demand (provisioning) + 1 Spot (ready) + 1 spot (provisioning).
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'echo "$s" | grep -q "1/3"',
            _check_replica_in_status(
                name,
                [
                    # The newly launched instance may transition to READY status
                    # quickly, so when checking status it could be either READY or
                    # LAUNCHING.
                    (2, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                    (1, False, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY')
                ]),

            # Wait until 2 spot instances are ready.
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            _check_replica_in_status(name, [(2, True, 'READY'),
                                            (0, False, '')]),
        ],
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}; '
        f'{_TEARDOWN_SERVICE.format(name=name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.no_do  # DO does not support `--cpus 2`
@pytest.mark.serve
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.no_remote_server
def test_skyserve_user_bug_restart(generic_cloud: str):
    """Tests that we restart the service after user bug."""
    # TODO(zhwu): this behavior needs some rethinking.
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    with smoke_tests_utils.increase_initial_delay_seconds_for_slow_cloud(
            generic_cloud) as increase_initial_delay_seconds:
        test = smoke_tests_utils.Test(
            'test-skyserve-user-bug-restart',
            [
                increase_initial_delay_seconds(
                    f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/restart/user_bug.yaml'
                ),
                f's=$(sky serve status {name}); echo "$s";'
                'until echo "$s" | grep -A 100 "Service Replicas" | grep "SHUTTING_DOWN"; '
                'do echo "Waiting for first service to be SHUTTING DOWN..."; '
                f'sleep 5; s=$(sky serve status {name}); echo "$s"; done; ',
                f's=$(sky serve status {name}); echo "$s";'
                'until echo "$s" | grep -A 100 "Service Replicas" | grep "FAILED"; '
                'do echo "Waiting for first service to be FAILED..."; '
                f'sleep 2; s=$(sky serve status {name}); echo "$s"; done; echo "$s"; '
                + _check_replica_in_status(name, [(1, True, 'FAILED')]) +
                # User bug failure will cause no further scaling.
                f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 1; '
                f'echo "$s" | grep -B 100 "NO_REPLICA" | grep "0/0"',
                increase_initial_delay_seconds(
                    f'sky serve update {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/auto_restart.yaml'
                ),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'until curl --connect-timeout 10 --max-time 10 $endpoint | grep "Hi, SkyPilot here"; do sleep 1; done; sleep 2; '
                + _check_replica_in_status(name, [(1, False, 'READY'),
                                                  (1, False, 'FAILED')]),
            ],
            _TEARDOWN_SERVICE.format(name=name),
            env=env,
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.serve
@pytest.mark.no_kubernetes  # Replicas on k8s may be running on the same node and have the same public IP
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
def test_skyserve_load_balancer(generic_cloud: str):
    """Test skyserve load balancer round-robin policy"""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-load-balancer',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/load_balancer/service.yaml',
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
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.serve
@pytest.mark.no_kubernetes
def test_skyserve_auto_restart():
    """Test skyserve with auto restart"""
    name = _get_service_name()
    zone = 'us-central1-a'
    test = smoke_tests_utils.Test(
        'test-skyserve-auto-restart',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            # TODO(tian): we can dynamically generate YAML from template to
            # avoid maintaining too many YAML files
            f'sky serve up -n {name} -y {smoke_tests_utils.LOW_RESOURCE_ARG} tests/skyserve/auto_restart.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl $endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
            # sleep for 20 seconds (initial delay) to make sure it will
            # be restarted
            'sleep 20',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=smoke_tests_utils.terminate_gcp_replica(name, zone, 1)),
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
            'request_output=$(curl $endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}; '
        f'{_TEARDOWN_SERVICE.format(name=name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_cancel(generic_cloud: str):
    """Test skyserve with cancel"""
    name = _get_service_name()

    test = smoke_tests_utils.Test(
        'test-skyserve-cancel',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/cancel/cancel.yaml',
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
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_streaming(generic_cloud: str):
    """Test skyserve with streaming"""
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-streaming',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/streaming/streaming.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'python3 tests/skyserve/streaming/send_streaming_request.py '
            '--endpoint $endpoint | grep "Streaming test passed"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        env=env,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
def test_skyserve_readiness_timeout_fail(generic_cloud: str):
    """Test skyserve with large readiness probe latency, expected to fail"""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-readiness-timeout-fail',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/readiness_timeout/task.yaml',
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
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_large_readiness_timeout(generic_cloud: str):
    """Test skyserve with customized large readiness timeout"""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-large-readiness-timeout',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/readiness_timeout/task_large_timeout.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'request_output=$(curl $endpoint); echo "$request_output"; echo "$request_output" | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.no_do  # DO does not support `--cpus 2`
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_update(generic_cloud: str):
    """Test skyserve with update"""
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    # Nebius takes longer to start instances.
    replica_check_timeout_seconds = 120 if generic_cloud == 'nebius' else 60
    test = smoke_tests_utils.Test(
        'test-skyserve-update',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/update/old.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl $endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --infra {generic_cloud} {resource_arg} --mode blue_green -y tests/skyserve/update/new.yaml',
            # sleep before update is registered.
            'sleep 20',
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'until curl $endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done;'
            # Make sure the traffic is not mixed
            'curl $endpoint | grep "Hi, new SkyPilot here"',
            # The latest 2 version should be READY and the older versions should be shutting down
            (_check_replica_in_status(
                name, [(2, False, 'READY'), (2, False, 'SHUTTING_DOWN')],
                timeout_seconds=replica_check_timeout_seconds) +
             _check_service_version(name, "2")),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
        env=env,
    )
    smoke_tests_utils.run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.no_do  # DO does not support `--cpus 2`
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_rolling_update(generic_cloud: str):
    """Test skyserve with rolling update"""
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    # Nebius takes longer to start instances.
    replica_check_timeout_seconds = 120 if generic_cloud == 'nebius' else 60
    single_new_replica = _check_replica_in_status(
        name, [(2, False, 'READY'), (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
               (1, False, 'SHUTTING_DOWN')],
        timeout_seconds=replica_check_timeout_seconds)
    with smoke_tests_utils.increase_initial_delay_seconds_for_slow_cloud(
            generic_cloud) as increase_initial_delay_seconds:
        test = smoke_tests_utils.Test(
            f'test-skyserve-rolling-update',
            [
                increase_initial_delay_seconds(
                    f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/update/old.yaml'
                ),
                _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl $endpoint | grep "Hi, SkyPilot here"',
                increase_initial_delay_seconds(
                    f'sky serve update {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/update/new.yaml'
                ),
                # Make sure the traffic is mixed across two versions, the replicas
                # with even id will sleep 120 seconds before being ready, so we
                # should be able to get observe the period that the traffic is mixed
                # across two versions.
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'until curl $endpoint | grep "Hi, new SkyPilot here!"; do sleep 2; done; sleep 2; '
                # The latest version should have one READY and the one of the older versions should be shutting down
                f'{single_new_replica} {_check_service_version(name, "1,2")} '
                # Check the output from the old version, immediately after the
                # output from the new version appears. This is guaranteed by the
                # round robin load balancing policy.
                # TODO(zhwu): we should have a more generalized way for checking the
                # mixed version of replicas to avoid depending on the specific
                # round robin load balancing policy.
                # Note: If there's a timeout waiting in single_new_replica check,
                # the round robin load balancing state might be affected and we may need
                # multiple requests to observe both old and new versions of the service.
                'result=$(curl $endpoint); echo "Result: $result"; '
                'if echo "$result" | grep -q "Hi, SkyPilot here"; then '
                '    echo "Found old version output"; '
                'else '
                '    echo "$result" | grep "Hi, new SkyPilot here!" || exit 1; '
                '    echo "Found new version output, trying again for old version"; '
                '    result2=$(curl $endpoint); echo "Result2: $result2"; '
                '    echo "$result2" | grep "Hi, SkyPilot here" || exit 1; '
                'fi',
            ],
            _TEARDOWN_SERVICE.format(name=name),
            timeout=20 * 60,
            env=env,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.serve
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.no_remote_server
def test_skyserve_fast_update(generic_cloud: str):
    """Test skyserve with fast update (Increment version of old replicas)"""
    name = _get_service_name()

    test = smoke_tests_utils.Test(
        'test-skyserve-fast-update',
        [
            f'sky serve up -n {name} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/skyserve/update/bump_version_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl $endpoint | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --mode blue_green -y tests/skyserve/update/bump_version_after.yaml',
            # sleep to wait for update to be registered.
            'sleep 40',
            # 2 on-demand (ready) + 1 on-demand (provisioning).
            (
                _check_replica_in_status(
                    name, [(2, False, 'READY'),
                           (1, False, _SERVICE_LAUNCHING_STATUS_REGEX)]) +
                # Fast update will directly have the latest version ready.
                _check_service_version(name, "2")),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=3) +
            _check_service_version(name, "2"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl $endpoint | grep "Hi, SkyPilot here"',
            # Test rolling update
            f'sky serve update {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/update/bump_version_before.yaml',
            # sleep to wait for update to be registered.
            'sleep 25',
            # 2 on-demand (ready) + 1 on-demand (shutting down).
            _check_replica_in_status(name, [(2, False, 'READY'),
                                            (1, False, 'SHUTTING_DOWN')]),
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "3"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; curl $endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_update_autoscale(generic_cloud: str):
    """Test skyserve update with autoscale"""
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    with smoke_tests_utils.increase_initial_delay_seconds_for_slow_cloud(
            generic_cloud) as increase_initial_delay_seconds:
        test = smoke_tests_utils.Test(
            f'test-skyserve-update-autoscale',
            [
                increase_initial_delay_seconds(
                    f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/update/num_min_two.yaml'
                ),
                _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
                _check_service_version(name, "1"),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'curl $endpoint | grep "Hi, SkyPilot here"',
                increase_initial_delay_seconds(
                    f'sky serve update {name} --infra {generic_cloud} {resource_arg} --mode blue_green -y tests/skyserve/update/num_min_one.yaml'
                ),
                # sleep before update is registered.
                'sleep 20',
                # Timeout will be triggered when update fails.
                _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1) +
                _check_service_version(name, "2"),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'curl $endpoint | grep "Hi, SkyPilot here!"',
                # Rolling Update
                increase_initial_delay_seconds(
                    f'sky serve update {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/update/num_min_two.yaml'
                ),
                # sleep before update is registered.
                'sleep 20',
                # Timeout will be triggered when update fails.
                _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
                _check_service_version(name, "3"),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'curl $endpoint | grep "Hi, SkyPilot here!"',
            ],
            _TEARDOWN_SERVICE.format(name=name),
            timeout=30 * 60,
            env=env,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Spot instances are not supported by Fluidstack
@pytest.mark.serve
@pytest.mark.no_kubernetes  # Spot instances are not supported in Kubernetes
@pytest.mark.no_do  # Spot instances not on DO
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
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
        # The two old on-demand instances will be in READY or SHUTTING_DOWN
        # status after autoscale.
        TWO_OLD_ON_DEMAND_INSTANCES_STATUS_AFTER_AUTOSCALE = r'READY\|SHUTTING_DOWN'
    else:
        # Check blue green update, it will keep both old on-demand instances
        # running, once there are 4 spot instance ready.
        update_check += [
            _check_replica_in_status(
                name, [(1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                       (2, False, 'READY')]) +
            _check_service_version(name, "1"),
        ]
        # The two old on-demand instances will be in READY status
        # after autoscale update.
        TWO_OLD_ON_DEMAND_INSTANCES_STATUS_AFTER_AUTOSCALE = 'READY'
    test = smoke_tests_utils.Test(
        f'test-skyserve-new-autoscaler-update-{mode}',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/update/new_autoscaler_before.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2) +
            _check_service_version(name, "1"),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            's=$(curl $endpoint); echo "$s"; echo "$s" | grep "Hi, SkyPilot here"',
            f'sky serve update {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --mode {mode} -y tests/skyserve/update/new_autoscaler_after.yaml',
            # Wait for update to be registered
            'sleep 90',
            wait_until_no_pending,
            _check_replica_in_status(name, [
                (4, True, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY'),
                (1, False, _SERVICE_LAUNCHING_STATUS_REGEX),
                (2, False, TWO_OLD_ON_DEMAND_INSTANCES_STATUS_AFTER_AUTOSCALE)
            ]),
            *update_check,
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=5),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
            _check_replica_in_status(name, [(4, True, 'READY'),
                                            (1, False, 'READY')]),
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# TODO: fluidstack does not support `--cpus 2`, but the check for services in this test is based on CPUs
@pytest.mark.no_fluidstack
@pytest.mark.no_do  # DO does not support `--cpus 2`
@pytest.mark.no_vast  # Vast doesn't support opening ports
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.serve
@pytest.mark.no_remote_server
def test_skyserve_failures(generic_cloud: str):
    """Test replica failure statuses"""
    resource_arg, env = smoke_tests_utils.get_cloud_specific_resource_config(
        generic_cloud)
    name = _get_service_name()
    with smoke_tests_utils.increase_initial_delay_seconds_for_slow_cloud(
            generic_cloud) as increase_initial_delay_seconds:
        test = smoke_tests_utils.Test(
            'test-skyserve-failures',
            [
                increase_initial_delay_seconds(
                    f'sky serve up -n {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/failures/initial_delay.yaml'
                ),
                f's=$(sky serve status {name}); '
                f'until echo "$s" | grep "FAILED_INITIAL_DELAY"; do '
                'echo "Waiting for replica to be failed..."; sleep 5; '
                f's=$(sky serve status {name}); echo "$s"; done;',
                'sleep 60',
                f'{_SERVE_STATUS_WAIT.format(name=name)}; echo "$s" | grep "{name}" | grep "FAILED_INITIAL_DELAY" | wc -l | grep 2; '
                # Make sure no new replicas are started for early failure.
                f'echo "$s" | grep -A 100 "Service Replicas" | grep "{name}" | wc -l | grep 2;',
                increase_initial_delay_seconds(
                    f'sky serve update {name} --infra {generic_cloud} {resource_arg} -y tests/skyserve/failures/probing.yaml'
                ),
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
                _check_replica_in_status(name, [
                    (1, False, 'FAILED_PROBING'),
                    (1, False, _SERVICE_LAUNCHING_STATUS_REGEX + '\|READY')
                ]),
                # TODO(zhwu): add test for FAILED_PROVISION
            ],
            _TEARDOWN_SERVICE.format(name=name),
            timeout=20 * 60,
            env=env,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.serve
@pytest.mark.resource_heavy
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
def test_skyserve_https(generic_cloud: str):
    """Test skyserve with https"""
    name = _get_service_name()

    keyfile = f'~/test-skyserve-key-{name}.pem'
    with tempfile.TemporaryDirectory() as tempdir:
        certfile = os.path.join(tempdir, 'cert.pem')
        subprocess_utils.run_no_outputs(
            f'openssl req -x509 -newkey rsa:2048 -days 36500 -nodes '
            f'-subj "/" -keyout {keyfile} -out {certfile}')

        test = smoke_tests_utils.Test(
            'test-skyserve-https',
            [
                f'sky serve up -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} -y tests/skyserve/https/service.yaml '
                f'--env TLS_KEYFILE_ENV_VAR={keyfile} --secret TLS_CERTFILE_ENV_VAR={certfile}',
                _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'output=$(curl $endpoint -k); echo $output; '
                'echo $output | grep "Hi, SkyPilot here"',
                # Self signed certificate should fail without -k.
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'output=$(curl $endpoint 2>&1); echo $output; '
                'echo $output | grep -E "self[ -]signed certificate"',
                # curl with wrong schema (http) should fail.
                f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
                'http_endpoint="${endpoint/https:/http:}"; '
                'output=$(curl $http_endpoint 2>&1); echo $output; '
                'echo $output | grep "Empty reply from server"',
            ],
            _TEARDOWN_SERVICE.format(name=name) + f'; rm -f {keyfile}',
            timeout=20 * 60,
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.serve
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support opening ports for skypilot yet
@pytest.mark.no_remote_server
def test_skyserve_multi_ports(generic_cloud: str):
    """Test skyserve with multiple ports"""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-multi-ports',
        [
            f'sky serve up -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y tests/skyserve/multi_ports.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $replica_endpoint | grep "Hi, SkyPilot here"; '
            f'export replica_endpoint=$(sky serve status {name} | tail -n 1 | awk \'{{print $4}}\'); '
            'export replica_endpoint_alt=$(echo $endpoint | sed "s/8080/8081/"); '
            'curl $replica_endpoint | grep "Hi, SkyPilot here"; '
            'curl $replica_endpoint_alt | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=20 * 60,
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


# TODO(Ziming, Tian): Add tests for autoscaling.


# ------- Testing user dependencies --------
@pytest.mark.no_vast  # Requires GCS
def test_user_dependencies(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'user-dependencies',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "pip install ray>2.11; ray start --head"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} "echo hi"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
            f'sky exec {name} "echo bye"',
            f'sky logs {name} 3 --status',
            f'sky launch -y -c {name} tests/test_yamls/different_default_conda_env.yaml',
            f'sky logs {name} 4 --status',
            # Launch again to test the default env does not affect SkyPilot
            # runtime setup
            f'sky launch -y -c {name} "python --version 2>&1 | grep \'Python 3.6\' || exit 1"',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_ha_kill_after_ready():
    """Test HA recovery when killing controller after replicas are READY."""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-ha-kill-after-ready',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch service and wait for ready
            f'sky serve up -n {name} -y tests/skyserve/high_availability/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            _check_replica_in_status(name, [(1, False, 'READY')]),
            # Verify service is accessible
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
            # Kill controller and verify recovery
            smoke_tests_utils.kill_and_wait_controller(name, 'serve'),
            # Verify service remains accessible after controller recovery
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            _check_replica_in_status(name, [(1, False, 'READY')]),
            # f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            # 'curl $endpoint | grep "Hi, SkyPilot here"',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
        env={
            skypilot_config.ENV_VAR_GLOBAL_CONFIG: 'tests/skyserve/high_availability/config.yaml'
        })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_ha_kill_during_provision():
    """Test HA recovery when killing controller during PROVISIONING."""
    name = _get_service_name()
    test = smoke_tests_utils.Test(
        'test-skyserve-ha-kill-during-provision',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch service and wait for provisioning
            f'sky serve up -n {name} -y tests/skyserve/high_availability/service.yaml',
            # Wait for service to enter PROVISIONING state
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'until echo "$s" | grep "PROVISIONING"; do '
            '  echo "Waiting for PROVISIONING state..."; '
            '  sleep 5; '
            f'  s=$(sky serve status {name}); '
            'done; echo "$s"',
            # Kill controller during provisioning
            smoke_tests_utils.kill_and_wait_controller(name, 'serve'),
            # Verify service eventually becomes ready
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            _check_replica_in_status(name, [(1, False, 'READY')]),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
            # Check there is only one cluster
            f'instance_names=$(gcloud compute instances list --filter="name~{name}" --format="value(name)"); '
            'echo "Initial instances: $instance_names"; '
            'num_instances=$(echo "$instance_names" | wc -l); '
            '[ "$num_instances" -eq "1" ] || (echo "Expected 1 instance, got $num_instances"; exit 1)',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
        env={
            skypilot_config.ENV_VAR_GLOBAL_CONFIG: 'tests/skyserve/high_availability/config.yaml'
        })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_ha_kill_during_pending():
    """Test HA recovery when killing controller during PENDING."""
    name = _get_service_name()
    replica_cluster_name = smoke_tests_utils.get_replica_cluster_name_on_gcp(
        name, 1)
    test = smoke_tests_utils.Test(
        'test-skyserve-ha-kill-during-pending',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch service and wait for pending
            f'sky serve up -n {name} -y tests/skyserve/high_availability/service.yaml',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; ',
            _check_replica_in_status(name, [(1, False, 'PENDING')]),
            # Kill controller during pending
            smoke_tests_utils.kill_and_wait_controller(name, 'serve'),
            # Verify service eventually becomes ready and accessible
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            _check_replica_in_status(name, [(1, False, 'READY')]),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
            # Check there are one cluster
            f'instance_names=$(gcloud compute instances list --filter="(labels.ray-cluster-name:{replica_cluster_name})" --format="value(name)"); '
            'echo "Initial instances: $instance_names"; '
            'num_instances=$(echo "$instance_names" | wc -l); '
            '[ "$num_instances" -eq "1" ] || (echo "Expected 1 instance, got $num_instances"; exit 1)',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
        env={
            skypilot_config.ENV_VAR_GLOBAL_CONFIG: 'tests/skyserve/high_availability/config.yaml'
        })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.gcp
@pytest.mark.serve
def test_skyserve_ha_kill_during_shutdown():
    """Test HA recovery when killing controller during replica shutdown."""
    name = _get_service_name()
    replica_cluster_name = smoke_tests_utils.get_replica_cluster_name_on_gcp(
        name, 1)
    test = smoke_tests_utils.Test(
        'test-skyserve-ha-kill-during-shutdown',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch service and wait for ready
            f'sky serve up -n {name} -y tests/skyserve/high_availability/service.yaml',
            _SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=1),
            f'{_SERVE_ENDPOINT_WAIT.format(name=name)}; '
            'curl $endpoint | grep "Hi, SkyPilot here"',
            # Record instance names and initiate shutdown of the replica
            f'instance_names=$(gcloud compute instances list --filter="(labels.ray-cluster-name:{replica_cluster_name})" --format="value(name)"); '
            'echo "Initial instances: $instance_names"; '
            'num_instances=$(echo "$instance_names" | wc -l); '
            '[ "$num_instances" -eq "1" ] || (echo "Expected 1 instance, got $num_instances"; exit 1)',
            # Shutdown the replica
            f'sky serve down -y {name} 1',
            # Verify the replica is in SHUTTING_DOWN state
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'until echo "$s" | grep -A 100 "Service Replicas" | grep "SHUTTING_DOWN" > /dev/null; do '
            '  echo "Waiting for replica to be SHUTTING_DOWN..."; sleep 5; '
            f'  s=$(sky serve status {name}); '
            'done; echo "$s"',
            # Kill controller during shutdown
            smoke_tests_utils.kill_and_wait_controller(name, 'serve'),
            # Even after the pod ready, `serve status` may return `Failed to connect to serve controller, please try again later.`
            # So we need to wait for a while before checking the status again.
            'sleep 10',
            f'{_SERVE_STATUS_WAIT.format(name=name)}; '
            'echo "$s" | grep -A 100 "Service Replicas" | grep "SHUTTING_DOWN" > /dev/null',
            # Verify GCP instances are eventually terminated
            # First check this command run well
            f'until gcloud compute instances list --filter="(labels.ray-cluster-name:{replica_cluster_name})" --format="value(name)" 2>/dev/null | wc -l | grep -q "^0$"; do '
            '  echo "Waiting for instances to terminate..."; sleep 5; '
            'done',
        ],
        _TEARDOWN_SERVICE.format(name=name),
        timeout=30 * 60,
        env={
            skypilot_config.ENV_VAR_GLOBAL_CONFIG: 'tests/skyserve/high_availability/config.yaml'
        })
    smoke_tests_utils.run_one_test(test)
