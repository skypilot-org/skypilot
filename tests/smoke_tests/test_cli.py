# Smoke tests for SkyPilot for CLI output
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_cli.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_cli.py --terminate-on-failure

import tempfile
import textwrap
from unittest import mock
from urllib import parse

import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

import sky
from sky import skypilot_config
from sky.client import sdk
from sky.server import common as server_common
from sky.skylet import constants
from sky.utils import common_utils

_CHECK_AWS_BUCKET_DOESNT_EXIST = (
    'aws s3api head-bucket --bucket {bucket_name} 2>/dev/null && exit 1 || exit 0'
)


@pytest.mark.no_remote_server
def test_endpoint_output_basic(generic_cloud: str):
    """Test that sky api info endpoint output is correct."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test('endpoint_output_basic', [
        f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set to default local API server."',
    ],
                                  timeout=smoke_tests_utils.get_timeout(
                                      generic_cloud),
                                  teardown=f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
def test_endpoint_output_basic_no_pg_conn_closed_errors(generic_cloud: str):
    """Test that sky api info endpoint output is correct and no pg conn closed errors are raised."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'endpoint_output_basic_no_pg_conn_closed_errors', [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT_NO_PG_CONN_CLOSED_ERROR}',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        teardown=f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
def test_endpoint_output_config(generic_cloud: str):
    """Test that sky api info endpoint output is correct when config is set."""

    endpoint = server_common.DEFAULT_SERVER_URL

    config = textwrap.dedent(f"""
    api_server:
        endpoint: {endpoint}
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test('endpoint_output_config', [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Endpoint set via {f.name}"',
        ],
                                      timeout=smoke_tests_utils.get_timeout(
                                          generic_cloud),
                                      teardown=f'sky down -y {name}',
                                      env={
                                          skypilot_config.ENV_VAR_GLOBAL_CONFIG:
                                              f.name
                                      })

        smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.no_remote_server
def test_endpoint_output_env(generic_cloud: str):
    """Test that sky api info output is correct when env endpoint is set."""
    name = smoke_tests_utils.get_cluster_name()
    expected_string = f"Endpoint set via the environment variable {constants.SKY_API_SERVER_URL_ENV_VAR}"
    test = smoke_tests_utils.Test('endpoint_output_env', [
        f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
        f's=$(SKYPILOT_DEBUG=0 sky api info | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "Expecting to see: {expected_string}\n" && echo "$s" | grep "{expected_string}"',
    ],
                                  timeout=smoke_tests_utils.get_timeout(
                                      generic_cloud),
                                  teardown=f'sky down -y {name}',
                                  env={
                                      constants.SKY_API_SERVER_URL_ENV_VAR:
                                          server_common.DEFAULT_SERVER_URL
                                  })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
def test_sky_logout_wih_env_endpoint(generic_cloud: str):
    """Test that sky api logout with env endpoint fails."""
    test = smoke_tests_utils.Test(
        'sky_logout_wih_env_endpoint', [
            f's=$(SKYPILOT_DEBUG=0 sky api logout 2>&1 | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Cannot logout of API server when the endpoint is set via the environment variable. Run unset"',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        env={
            constants.SKY_API_SERVER_URL_ENV_VAR: "https://SUPERFAKE_ENDPOINT.unreachable"
        })
    smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.no_remote_server
def test_sky_login_wih_env_endpoint(generic_cloud: str):
    """Test that sky api login with env endpoint fails."""
    test = smoke_tests_utils.Test(
        'sky_login_wih_env_endpoint', [
            f's=$(SKYPILOT_DEBUG=0 sky api login -e https://SUPERFAKE_ENDPOINT.unreachable 2>&1 | tee /dev/stderr) && echo "\n===Validating endpoint output===" && echo "$s" | grep "Cannot login to API server when the endpoint is set via the environment variable. Run unset"',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        env={
            constants.SKY_API_SERVER_URL_ENV_VAR: "https://SUPERFAKE_ENDPOINT.unreachable"
        })
    smoke_tests_utils.run_one_test(test, check_sky_status=False)


@pytest.mark.no_remote_server
def test_cli_invalid_config_details(generic_cloud: str):
    """Test that invalid config overrides surface detailed CLI errors."""
    invalid_override = 'gcp.label.smoke-test=test-value'
    details_msg = 'Details: Invalid config YAML from (CLI).'
    suggestion_msg = "Instead of 'label', did you mean 'labels'?"
    command = (
        's=$(SKYPILOT_DEBUG=0 sky launch --config '
        f'{invalid_override} tests/test_yamls/minimal.yaml 2>&1 | tee /dev/stderr) && '
        'echo "\\n===Validating config error details===\\n" && '
        f'echo "$s" | grep "{details_msg}" && '
        f'echo "$s" | grep "{suggestion_msg}"')

    test = smoke_tests_utils.Test(
        'cli_invalid_config_details', [command],
        timeout=smoke_tests_utils.get_timeout(generic_cloud))
    smoke_tests_utils.run_one_test(test, check_sky_status=False)


def test_cli_auto_retry(generic_cloud: str):
    """Test that cli auto retry works."""
    name = smoke_tests_utils.get_cluster_name()
    port = common_utils.find_free_port(23456)
    server_url = smoke_tests_utils.get_api_server_url()
    parsed = parse.urlparse(server_url)
    api_proxy_url = f'http://127.0.0.1:{port}'
    if parsed.username and parsed.password:
        api_proxy_url = f'http://{parsed.username}:{parsed.password}@127.0.0.1:{port}'
    run_command = 'for i in {1..120}; do echo "output $i" && sleep 1; done'
    test = smoke_tests_utils.Test(
        'cli_auto_retry',
        [
            # Chaos proxy will kill TCP connections every 30 seconds.
            f'python tests/chaos/chaos_proxy.py --port {port} --interval 30 & echo $! > /tmp/{name}-chaos.pid',
            # Both launch streaming and logs streaming should survive the chaos.
            f'SKYPILOT_API_SERVER_ENDPOINT={api_proxy_url} sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} \'{run_command}\'',
            f'kill $(cat /tmp/{name}-chaos.pid)',
        ],
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
        teardown=f'sky down -y {name}; kill $(cat /tmp/{name}-chaos.pid) || true'
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_storage_delete(generic_cloud: str):
    """Test that storage delete works."""
    name = smoke_tests_utils.get_cluster_name()
    bucket_name = f'{name}-bucket'
    bucket_job_yaml = textwrap.dedent(f"""
    name: {name}-job
    resources:
        cpus: 2
        infra: aws
    file_mounts:
        /output:
            name: {bucket_name}
            mode: MOUNT
            store: s3
    run: |
        echo "Data" > /output/data.txt
    """)
    with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
        job_yaml.write(bucket_job_yaml.encode('utf-8'))
        job_yaml.flush()

        test = smoke_tests_utils.Test('storage_delete', [
            f'echo "bucket name: {bucket_name}"',
            smoke_tests_utils.launch_cluster_for_cloud_cmd(
                'aws', name, skip_remote_server_check=True),
            f's=$(SKYPILOT_DEBUG=0 sky jobs launch -y {job_yaml.name}) && echo "$s" | grep "Job finished (status: SUCCEEDED)."',
            f's=$(SKYPILOT_DEBUG=0 sky storage delete -y {bucket_name}) && echo "$s" && echo "$s" | grep "Deleted S3 bucket {bucket_name}"',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=_CHECK_AWS_BUCKET_DOESNT_EXIST.format(
                    bucket_name=bucket_name)),
        ],
                                      teardown=smoke_tests_utils.
                                      down_cluster_for_cloud_cmd(
                                          name, skip_remote_server_check=True),
                                      timeout=smoke_tests_utils.get_timeout(
                                          generic_cloud))
        smoke_tests_utils.run_one_test(test, check_sky_status=False)


def test_debug_dump_recent(generic_cloud: str):
    """Test sky debug-dump --recent flag creates a valid dump."""
    test = smoke_tests_utils.Test(
        'debug_dump_recent',
        [
            # Create a debug dump with --recent (no clusters/jobs needed)
            'sky debug-dump --recent 1 --output /tmp/test_debug_dump_recent.zip',
            # Verify the zip file was created and is a valid zip
            'test -f /tmp/test_debug_dump_recent.zip',
            's=$(unzip -l /tmp/test_debug_dump_recent.zip) && echo "$s" && '
            'echo "$s" | grep "summary.json" && '
            'echo "$s" | grep "server_info.json" && '
            'echo "$s" | grep "errors.json"',
            # Extract and verify summary.json structure
            'unzip -o /tmp/test_debug_dump_recent.zip'
            ' -d /tmp/test_debug_dump_recent && '
            'cd /tmp/test_debug_dump_recent/debug_dump_* && '
            's=$(cat summary.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            'assert \\\"requested\\\" in d; '
            'assert \\\"collected\\\" in d; '
            'assert d[\\\"collected\\\"][\\\"request_count\\\"] > 0, '
            '\\\"system daemon requests should always be collected\\\"; '
            '"',
            # Verify server_info.json has enriched fields
            'cd /tmp/test_debug_dump_recent/debug_dump_* && '
            's=$(cat server_info.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            'assert \\\"skypilot_version\\\" in d; '
            'assert \\\"python_version\\\" in d; '
            'assert \\\"os_platform\\\" in d; '
            'assert \\\"dump_timestamp_human\\\" in d; '
            'assert isinstance(d[\\\"enabled_clouds\\\"], dict), '
            '\\\"enabled_clouds should be a dict keyed by workspace\\\"; '
            'assert len(d[\\\"enabled_clouds\\\"]) > 0; '
            '"',
        ],
        teardown='rm -f /tmp/test_debug_dump_recent.zip && '
        'rm -rf /tmp/test_debug_dump_recent',
        timeout=2 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def test_debug_dump_cluster(generic_cloud: str):
    """Test sky debug-dump -c flag with a real cluster."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'debug_dump_cluster',
        [
            # Launch a minimal cluster
            f'sky launch -y -c {name}'
            f' {smoke_tests_utils.LOW_RESOURCE_ARG}'
            f' --infra {generic_cloud} tests/test_yamls/minimal.yaml',
            # Create a debug dump for the cluster
            f'sky debug-dump -c {name}'
            ' --output /tmp/test_debug_dump_cluster.zip',
            # Verify the cluster directory exists in the dump
            's=$(unzip -l /tmp/test_debug_dump_cluster.zip) && echo "$s" && '
            f'echo "$s" | grep "clusters/{name}/cluster_info.json" && '
            'echo "$s" | grep "requests/" && '
            'echo "$s" | grep "summary.json"',
            # Extract and verify cluster_info.json
            'unzip -o /tmp/test_debug_dump_cluster.zip'
            ' -d /tmp/test_debug_dump_cluster && '
            'cd /tmp/test_debug_dump_cluster/debug_dump_* && '
            f's=$(cat clusters/{name}/cluster_info.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            'assert \\\"cluster_name\\\" in d; '
            'assert \\\"status\\\" in d; '
            '"',
            # Verify summary shows the cluster was collected
            'cd /tmp/test_debug_dump_cluster/debug_dump_* && '
            's=$(cat summary.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            f'assert \\\"{name}\\\" in d[\\\"collected\\\"][\\\"cluster_names\\\"]; '
            'assert d[\\\"collected\\\"][\\\"cluster_count\\\"] >= 1; '
            # Cross-linked requests from the launch should be present
            'assert d[\\\"collected\\\"][\\\"request_count\\\"] > 0; '
            '"',
        ],
        teardown=f'sky down -y {name} && '
        'rm -f /tmp/test_debug_dump_cluster.zip && '
        'rm -rf /tmp/test_debug_dump_cluster',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


def test_debug_dump_request_id(generic_cloud: str):
    """Test sky debug-dump -r flag with a real request ID."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'debug_dump_request_id',
        [
            # Launch a cluster and capture the request ID
            f'sky launch -y -c {name} --async'
            f' {smoke_tests_utils.LOW_RESOURCE_ARG}'
            f' --infra {generic_cloud} tests/test_yamls/minimal.yaml'
            ' | tee /tmp/test_debug_dump_reqid_launch.txt',
            # Extract request ID from async output
            'req_id=$(grep "Request ID:" /tmp/test_debug_dump_reqid_launch.txt'
            ' | head -1 | sed "s/.*Request ID: //") && '
            'echo "Captured request ID: $req_id" && '
            'test -n "$req_id" && '
            # Wait for the request to finish
            f'sky launch -y -c {name}'
            f' {smoke_tests_utils.LOW_RESOURCE_ARG}'
            f' --infra {generic_cloud} tests/test_yamls/minimal.yaml',
            # Create a debug dump for the request ID
            'req_id=$(grep "Request ID:" /tmp/test_debug_dump_reqid_launch.txt'
            ' | head -1 | sed "s/.*Request ID: //") && '
            'sky debug-dump -r "$req_id"'
            ' --output /tmp/test_debug_dump_reqid.zip',
            # Verify the request directory exists in the dump
            'req_id=$(grep "Request ID:" /tmp/test_debug_dump_reqid_launch.txt'
            ' | head -1 | sed "s/.*Request ID: //") && '
            's=$(unzip -l /tmp/test_debug_dump_reqid.zip) && echo "$s" && '
            'echo "$s" | grep "requests/$req_id/request_info.json"',
            # Verify request_info.json contents
            'req_id=$(grep "Request ID:" /tmp/test_debug_dump_reqid_launch.txt'
            ' | head -1 | sed "s/.*Request ID: //") && '
            'unzip -o /tmp/test_debug_dump_reqid.zip'
            ' -d /tmp/test_debug_dump_reqid && '
            'cd /tmp/test_debug_dump_reqid/debug_dump_* && '
            's=$(cat requests/$req_id/request_info.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            'assert \\\"request_id\\\" in d; '
            'assert \\\"name\\\" in d; '
            'assert \\\"status\\\" in d; '
            '"',
        ],
        teardown=f'sky down -y {name} && '
        'rm -f /tmp/test_debug_dump_reqid.zip '
        '/tmp/test_debug_dump_reqid_launch.txt && '
        'rm -rf /tmp/test_debug_dump_reqid',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


def test_debug_dump_job(generic_cloud: str):
    """Test sky debug-dump -j flag with a real managed job."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'debug_dump_job',
        [
            # Launch a managed job
            f'sky jobs launch -y -n {name}'
            f' {smoke_tests_utils.LOW_RESOURCE_ARG}'
            f' --infra {generic_cloud} -- echo hello',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            # Get the job ID
            f'job_id=$(sky jobs queue | grep {name}'
            ' | head -1 | awk \'{print $1}\') && '
            'echo "Job ID: $job_id" && test -n "$job_id" && '
            # Create a debug dump for the managed job
            'sky debug-dump -j "$job_id"'
            ' --output /tmp/test_debug_dump_job.zip',
            # Verify the managed_jobs directory exists in the dump
            f'job_id=$(sky jobs queue | grep {name}'
            ' | head -1 | awk \'{print $1}\') && '
            's=$(unzip -l /tmp/test_debug_dump_job.zip) && echo "$s" && '
            'echo "$s" | grep "managed_jobs/" && '
            'echo "$s" | grep "summary.json"',
            # Verify summary shows the managed job was collected
            'unzip -o /tmp/test_debug_dump_job.zip'
            ' -d /tmp/test_debug_dump_job && '
            'cd /tmp/test_debug_dump_job/debug_dump_* && '
            's=$(cat summary.json) && echo "$s" && '
            'echo "$s" | python3 -c "'
            'import sys, json; d = json.load(sys.stdin); '
            'assert d[\\\"collected\\\"][\\\"managed_job_count\\\"] >= 1; '
            '"',
        ],
        teardown=f'sky jobs cancel -y -n {name} || true && '
        'rm -f /tmp/test_debug_dump_job.zip && '
        'rm -rf /tmp/test_debug_dump_job',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + 2 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def test_debug_dump_no_args(generic_cloud: str):
    """Test that sky debug-dump with no arguments shows usage error."""
    test = smoke_tests_utils.Test(
        'debug_dump_no_args',
        [
            'sky debug-dump > /tmp/test_debug_dump_noargs.txt 2>&1;'
            ' grep -qi "at least one of" /tmp/test_debug_dump_noargs.txt',
        ],
        teardown='rm -f /tmp/test_debug_dump_noargs.txt',
        timeout=30,
    )
    smoke_tests_utils.run_one_test(test)
