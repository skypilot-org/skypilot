"""Smoke tests for SkyPilot centralized log collection."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky


def _get_wait_for_gcp_logs_cmd(log_filter: str) -> str:
    return textwrap.dedent(f"""\
        for attempt in {{1..18}}; do
          output=$(gcloud logging read '{log_filter}' --order=asc --format=json)
          rc=$?
          if [ "$rc" -ne 0 ]; then
            echo "gcloud logging read failed with exit code $rc"
            exit "$rc"
          fi

          all_logs_found=true
          for i in {{1..10}}; do
            expected_log='"log": "test output '$i'"'
            if ! grep -q "$expected_log" <<< "$output"; then
              all_logs_found=false
              break
            fi
          done
          if [ "$all_logs_found" = true ]; then
            echo "$output"
            echo "===Validated logs from GCP Cloud Logging==="
            exit 0
          fi

          if [ "$attempt" -eq 18 ]; then
            echo "$output"
            echo "Timed out waiting for logs in GCP Cloud Logging"
            exit 1
          fi
          sleep 10
        done
        """)


@pytest.mark.no_vast  # Requires GCP
@pytest.mark.no_shadeform  # Requires GCP
@pytest.mark.no_fluidstack  # Requires GCP to be enabled
@pytest.mark.no_nebius  # Requires GCP to be enabled
@pytest.mark.no_kubernetes  # Requires GCP to be enabled
@pytest.mark.no_seeweb  # Requires GCP to be enabled
def test_log_collection_to_gcp(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Calculate timestamp 1 hour ago in ISO format
    one_hour_ago = (datetime.now(timezone.utc) -
                    timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with tempfile.NamedTemporaryFile(mode='w') as base, \
        tempfile.NamedTemporaryFile(mode='w') as additional_labels:
        base.write(
            textwrap.dedent("""\
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
        logs_cmd = 'for i in {1..10}; do echo "test output $i"; done'
        test = smoke_tests_utils.Test(
            'log_collection_to_gcp',
            [
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} '
                    f'{smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                f'sky logs {name} 1',
                _get_wait_for_gcp_logs_cmd(
                    f'labels.skypilot_cluster_name={name} AND '
                    f'timestamp>="{one_hour_ago}"'),
                smoke_tests_utils.with_config(
                    f'sky jobs launch -y -n {name}-job '
                    f'--infra {generic_cloud} '
                    f'{smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    base.name),
                _get_wait_for_gcp_logs_cmd(
                    f'jsonPayload.log_path:{name}-job AND '
                    f'timestamp>="{one_hour_ago}"'),
                f'sky down -y {name}',
                smoke_tests_utils.with_config(
                    f'sky launch -y -c {name} --infra {generic_cloud} '
                    f'{smoke_tests_utils.LOW_RESOURCE_ARG} \'{logs_cmd}\'',
                    additional_labels.name),
                _get_wait_for_gcp_logs_cmd(
                    f'labels.skypilot_smoke_test_case={name}-case AND '
                    f'timestamp>="{one_hour_ago}"'),
            ],
            f'sky down -y {name}',
            timeout=smoke_tests_utils.LOG_STORE_CMD_TIMEOUT,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Requires GCP
@pytest.mark.no_shadeform  # Requires GCP
@pytest.mark.no_fluidstack  # Requires GCP to be enabled
@pytest.mark.no_nebius  # Requires GCP to be enabled
@pytest.mark.no_kubernetes  # Requires GCP to be enabled
@pytest.mark.no_seeweb  # Requires GCP to be enabled
def test_managed_job_logs_with_log_store(generic_cloud: str):
    """`sky jobs logs` works for running and terminal jobs with a log store.

    When a log store is configured, job logs are forwarded to it. `sky jobs
    logs` must keep working in both states: streaming from the cluster while the
    job is running, and returning a finished job's logs afterwards (served from
    the controller's local copy when the store has no read-back path).
    """
    name = smoke_tests_utils.get_cluster_name()
    job_name = f'{name}-job'
    marker = 'SKY_JOBS_LOG_STORE_LINE'
    # Print a marker every 2s for ~90s so the job has a running window.
    run_cmd = f'for i in $(seq 1 45); do echo "{marker}_$i"; sleep 2; done'
    # Resolve the job id by name from any column of `sky jobs queue --all`.
    get_job_id_cmd = (
        's=$(sky jobs queue --all); echo "$s" | '
        'awk -v n=' + job_name +
        ' \'{for (i=1; i<=NF; i++) if ($i==n) {print $1; break}}\' | '
        'sort -un | head -1')
    wait_for_managed_job = (
        smoke_tests_utils.
        get_cmd_wait_until_managed_job_status_contains_matching_job_name)
    with tempfile.NamedTemporaryFile(mode='w') as base:
        base.write(
            textwrap.dedent("""\
            logs:
              store: gcp
            """))
        base.flush()
        test = smoke_tests_utils.Test(
            'managed_job_logs_with_log_store',
            [
                smoke_tests_utils.with_config(
                    f'sky jobs launch -y -d -n {job_name} '
                    f'--infra {generic_cloud} '
                    f'{smoke_tests_utils.LOW_RESOURCE_ARG} \'{run_cmd}\'',
                    base.name),
                # Running state: logs stream from the cluster.
                wait_for_managed_job(
                    job_name=job_name,
                    job_status=[sky.ManagedJobStatus.RUNNING],
                    timeout=smoke_tests_utils.LOG_STORE_JOB_START_TIMEOUT),
                'sleep 10',
                f's=$(sky jobs logs -n {job_name} --no-follow); echo "$s"; '
                f'echo "$s" | grep "{marker}_1"',
                # Terminal state: logs are still retrievable (served from the
                # controller-local copy when the store has no read-back path).
                wait_for_managed_job(
                    job_name=job_name,
                    job_status=[sky.ManagedJobStatus.SUCCEEDED],
                    timeout=360),
                f's=$(sky jobs logs $({get_job_id_cmd}) --no-follow); '
                f'echo "$s"; echo "$s" | grep "{marker}_1"',
            ],
            f'sky jobs cancel -y -n {job_name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=smoke_tests_utils.LOG_STORE_CMD_TIMEOUT,
        )
        smoke_tests_utils.run_one_test(test)
