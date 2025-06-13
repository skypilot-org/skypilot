"""Smoke tests for SkyPilot centralized log collection."""

import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


@pytest.mark.no_vast  # Requires GCP
@pytest.mark.no_fluidstack  # Requires GCP to be enabled
def test_log_collection_to_gcp(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(
            textwrap.dedent(f"""\
                logs:
                  store: gcp
                """))
        f.flush()
        logs_cmd = 'for i in {1..10}; do echo $i; done'
        validate_logs_cmd = (
            'echo $output && echo "===Validate logs from GCP Cloud Logging===" &&'
            'for i in {1..10}; echo $output | grep $i; done')
        test = smoke_tests_utils.Test(
            'log_collection_to_gcp',
            [
                f'export {skypilot_config.ENV_VAR_GLOBAL_CONFIG}={f.name}; ',
                f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                f'sky logs {name} 1',
                (f'output=$(gcloud logging read "labels.skypilot_cluster_name={name}" --format=json | jq -r ".[].jsonPayload.log") && '
                 f'{validate_logs_cmd}'),
                f'sky jobs launch -y -n {name}-job -j {name}-0 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "{logs_cmd}"',
                f'sky jobs logs {name}-job 1',
                (f'output=$(gcloud logging read "labels.skypilot_cluster_name={name}-job" --format=json | jq -r ".[].jsonPayload.log") && '
                 f'{validate_logs_cmd}'),
            ],
            # f'sky down -y {name}',
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)
