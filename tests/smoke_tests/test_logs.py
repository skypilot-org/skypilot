"""Smoke tests for SkyPilot centralized log collection."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.no_vast  # Requires GCP
@pytest.mark.no_shadeform  # Requires GCP
@pytest.mark.no_fluidstack  # Requires GCP to be enabled
@pytest.mark.no_nebius  # Requires GCP to be enabled
@pytest.mark.no_kubernetes  # Requires GCP to be enabled
def test_log_collection_to_gcp(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Calculate timestamp 1 hour ago in ISO format
    one_hour_ago = (datetime.now(timezone.utc) -
                    timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with tempfile.NamedTemporaryFile(mode='w') as base, \
        tempfile.NamedTemporaryFile(mode='w') as additional_labels:
        base.write(
            textwrap.dedent(f"""\
                logs:
                  store: gcp
                """))
        base.flush()
        additional_labels.write(
            textwrap.dedent(f"""\
                logs:
                  store: gcp
                  gcp:
                    additional_labels:
                      skypilot_smoke_test_case: {name}-case
                """))
        additional_labels.flush()
        logs_cmd = 'for i in {1..10}; do echo $i; done'
        validate_logs_cmd = (
            'echo $output && echo "===Validate logs from GCP Cloud Logging===" && '
            'for i in {1..10}; do echo $output | grep -q $i; done')
        test = smoke_tests_utils.Test(
            'log_collection_to_gcp',
            [
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                f'sky logs {name} 1',
                # Wait for the logs to be available in the GCP Cloud Logging.
                'sleep 10',
                # Use grep instead of jq to avoid the dependency on jq.
                (f'output=$(gcloud logging read \'labels.skypilot_cluster_name={name} AND timestamp>="{one_hour_ago}"\' --order=asc --format=json | grep \'"log":\') && '
                 f'{validate_logs_cmd}'),
                smoke_tests_utils.with_config(
                    f'sky jobs launch -y -n {name}-job --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                'sleep 10',
                (f'output=$(gcloud logging read \'jsonPayload.log_path:{name}-job AND timestamp>="{one_hour_ago}"\' --order=asc --format=json | grep \'"log":\') && '
                 f'{validate_logs_cmd}'),
                f'sky down -y {name}',
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    additional_labels.name),
                'sleep 10',
                (f'output=$(gcloud logging read \'labels.skypilot_smoke_test_case={name}-case AND timestamp>="{one_hour_ago}"\' --order=asc --format=json | grep \'"log":\') && '
                 f'{validate_logs_cmd}'),
            ],
            f'sky down -y {name}',
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)
