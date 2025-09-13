"""Smoke tests for SkyPilot AWS CloudWatch log collection."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


@pytest.mark.no_vast  # Requires AWS
@pytest.mark.no_fluidstack  # Requires AWS to be enabled
@pytest.mark.no_nebius  # Requires AWS to be enabled
@pytest.mark.no_seeweb  # Requires AWS to be enabled
def test_log_collection_to_aws_cloudwatch(generic_cloud: str):
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip(
            'Skipping test in shared remote api server environment as '
            'helm api server deployment set credential_file instead of env vars'
        )
    name = smoke_tests_utils.get_cluster_name()
    # Calculate timestamp in milliseconds 1 hour ago via portable shell command
    one_hour_ago = '$(( $(date +%s) - 3600 ))000'
    with tempfile.NamedTemporaryFile(mode='w') as base, \
        tempfile.NamedTemporaryFile(mode='w') as additional_tags:
        base.write(
            textwrap.dedent(f"""\
                logs:
                  store: aws
                  aws:
                    region: us-east-1
                """))
        base.flush()
        additional_tags.write(
            textwrap.dedent(f"""\
                logs:
                  store: aws
                  aws:
                    region: us-east-1
                    additional_tags:
                      skypilot_smoke_test_case: {name}-case
                """))
        additional_tags.flush()
        logs_cmd = 'for i in {1..10}; do echo $i; done'
        validate_logs_cmd = (
            'echo $output && echo "===Validate logs from AWS CloudWatch===" && '
            'for i in {1..10}; do echo $output | grep -q $i; done')
        test = smoke_tests_utils.Test(
            'log_collection_to_aws_cloudwatch',
            [
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                f'sky logs {name} 1',
                # Wait for the logs to be available in AWS CloudWatch.
                'sleep 10',
                # Use AWS CLI to query CloudWatch logs
                (f'output=$(aws logs --region us-east-1 filter-log-events --log-group-name skypilot-logs '
                 f'--filter-pattern \'{{ $.\'"[\'skypilot.cluster_name\']"\' = "{name}" }}\' '
                 f'--start-time {one_hour_ago} '
                 f'--query "events[*].message" --output text) && '
                 f'{validate_logs_cmd}'),
                smoke_tests_utils.with_config(
                    f'sky jobs launch -y -n {name}-job --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                'sleep 10',
                # Query logs for the job
                (f'output=$(aws --region us-east-1 logs filter-log-events --log-group-name skypilot-logs '
                 f'--filter-pattern "%{name}-job%" '
                 f'--start-time {one_hour_ago} '
                 f'--query "events[*].message" --output text) && '
                 f'{validate_logs_cmd}'),
                f'sky down -y {name}',
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    additional_tags.name),
                'sleep 10',
                # Query logs with additional tags
                (f'output=$(aws --region us-east-1 logs filter-log-events --log-group-name skypilot-logs '
                 f'--filter-pattern \'{{ $.skypilot_smoke_test_case = "{name}-case" }}\' '
                 f'--start-time {one_hour_ago} '
                 f'--query "events[*].message" --output text) && '
                 f'{validate_logs_cmd}'),
            ],
            f'sky down -y {name}',
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)
