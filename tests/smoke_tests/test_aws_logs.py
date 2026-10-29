"""Smoke tests for SkyPilot AWS CloudWatch log collection."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import os

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.no_vast  # Requires AWS
@pytest.mark.no_shadeform  # Requires AWS
@pytest.mark.no_fluidstack  # Requires AWS to be enabled
@pytest.mark.no_nebius  # Requires AWS to be enabled
@pytest.mark.no_seeweb  # Requires AWS to be enabled
# logs is a server-side setting: the test restarts the API server with it.
@pytest.mark.no_remote_server
@pytest.mark.no_dependency  # We can't restart the api server in the dependency test.
def test_log_collection_to_aws_cloudwatch(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Calculate timestamp in milliseconds 1 hour ago via portable shell command
    one_hour_ago = '$(( $(date +%s) - 3600 ))000'
    base = smoke_tests_utils.write_api_server_config(
        {'logs': {
            'store': 'aws',
            'aws': {
                'region': 'us-east-1'
            }
        }})
    additional_tags = smoke_tests_utils.write_api_server_config({
        'logs': {
            'store': 'aws',
            'aws': {
                'region': 'us-east-1',
                'additional_tags': {
                    'skypilot_smoke_test_case': f'{name}-case'
                }
            }
        }
    })
    logs_cmd = 'for i in {1..10}; do echo "test output $i"; done'
    validate_logs_cmd = (
        'echo $output && echo "===Validate logs from AWS CloudWatch===" && '
        'for i in {1..10}; do echo $output | grep -q "test output $i"; done')
    test = smoke_tests_utils.Test(
        'log_collection_to_aws_cloudwatch',
        [
            smoke_tests_utils.restart_api_server_with_config(base),
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
            f'sky logs {name} 1',
            # Wait for the logs to be available in AWS CloudWatch.
            'sleep 10',
            # Use AWS CLI to query CloudWatch logs
            (f'output=$(aws logs --region us-east-1 filter-log-events --log-group-name skypilot-logs '
             f'--filter-pattern \'{{ $.\'"[\'skypilot.cluster_name\']"\' = "{name}" }}\' '
             f'--start-time {one_hour_ago} '
             f'--query "events[*].message" --output text) && '
             f'{validate_logs_cmd}'),
            f'sky jobs launch -y -n {name}-job --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
            'sleep 10',
            # Query logs for the job
            (f'output=$(aws --region us-east-1 logs filter-log-events --log-group-name skypilot-logs '
             f'--filter-pattern "%{name}-job%" '
             f'--start-time {one_hour_ago} '
             f'--query "events[*].message" --output text) && '
             f'{validate_logs_cmd}'),
            f'sky down -y {name}',
            smoke_tests_utils.restart_api_server_with_config(additional_tags),
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
            'sleep 10',
            # Query logs with additional tags
            (f'output=$(aws --region us-east-1 logs filter-log-events --log-group-name skypilot-logs '
             f'--filter-pattern \'{{ $.skypilot_smoke_test_case = "{name}-case" }}\' '
             f'--start-time {one_hour_ago} '
             f'--query "events[*].message" --output text) && '
             f'{validate_logs_cmd}'),
        ],
        f'sky down -y {name}; {smoke_tests_utils.RESTORE_API_SERVER_CONFIG}',
        timeout=smoke_tests_utils.LOG_STORE_CMD_TIMEOUT,
    )
    smoke_tests_utils.run_one_test(test)
    os.unlink(base)
    os.unlink(additional_tags)
