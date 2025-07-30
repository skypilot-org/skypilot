# Smoke tests for SkyPilot for managed jobs
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_managed_job.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_managed_job.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_managed_job.py::test_managed_jobs
#
# Only run managed job tests
# > pytest tests/smoke_tests/test_managed_job.py --managed-jobs
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_managed_job.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_managed_job.py --generic-cloud aws
import pathlib
import re
import tempfile
import time

import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests import test_mount_and_storage

import sky
from sky import jobs
from sky import skypilot_config
from sky.clouds import gcp
from sky.data import storage as storage_lib
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import controller_utils


# ---------- Testing managed job ----------
# TODO(zhwu): make the jobs controller on GCP, to avoid parallel test issues
# when the controller being on Azure, which takes a long time for launching
# step.
@pytest.mark.managed_jobs
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers and auto-stop
def test_managed_jobs_basic(generic_cloud: str):
    """Test the managed jobs yaml."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'managed-jobs',
        [
            f'sky jobs launch -n {name}-1 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/managed_job.yaml -y -d',
            f'sky jobs launch -n {name}-2 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/managed_job.yaml -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-1',
                job_status=[
                    sky.ManagedJobStatus.PENDING,
                    sky.ManagedJobStatus.DEPRECATED_SUBMITTED,
                    sky.ManagedJobStatus.STARTING, sky.ManagedJobStatus.RUNNING
                ],
                timeout=60),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-2',
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=360
                if generic_cloud in ['azure', 'kubernetes', 'nebius'] else 120),
            f'sky jobs cancel -y -n {name}-1',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-1',
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=230),
            # Test the functionality for logging.
            f's=$(sky jobs logs -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "start counting"',
            f's=$(sky jobs logs --controller -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "Cluster launched:"',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        # TODO(zhwu): Change to f'sky jobs cancel -y -n {name}-1 -n {name}-2' when
        # canceling multiple job names is supported.
        f'sky jobs cancel -y -n {name}-1; sky jobs cancel -y -n {name}-2',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers and auto-stop
def test_managed_jobs_cli_exit_codes(generic_cloud: str):
    """Test that managed jobs CLI commands properly return exit codes based on job success/failure."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'managed_jobs_exit_codes',
        [
            # Test jobs launch with successful job
            f'sky jobs launch -y -n jobs-{name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo jobs success" && echo "Jobs launch exit code: $?"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'jobs-{name}',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=60),

            # Get job ID from the queue and test logs with successful job
            f'JOB_ROW=$(sky jobs queue | grep jobs-{name} | head -n1) && '
            f'echo "$JOB_ROW" && '
            f'JOB_ID=$(echo "$JOB_ROW" | awk \'{{print $1}}\') && '
            f'echo "JOB_ID=$JOB_ID" && '
            f'sky jobs logs $JOB_ID && echo "Jobs logs exit code: $?"',

            # Test jobs launch with failing job
            f'sky jobs launch -y -n jobs-fail-{name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "exit 1" || echo "Jobs launch failed exit code: $?" | grep "Jobs launch failed exit code: 100"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'jobs-fail-{name}',
                job_status=[sky.ManagedJobStatus.FAILED],
                timeout=60),

            # Get job ID from the queue and test logs with failed job
            f'JOB_ROW=$(sky jobs queue | grep jobs-fail-{name} | head -n1) && '
            f'echo "$JOB_ROW" && '
            f'JOB_ID=$(echo "$JOB_ROW" | awk \'{{print $1}}\') && '
            f'echo "JOB_ID=$JOB_ID" && '
            f'sky jobs logs $JOB_ID || echo "Failed jobs logs exit code: $?" | grep "Failed jobs logs exit code: 100"',
        ],
        f'sky jobs cancel -y -n jobs-{name}; sky jobs cancel -y -n jobs-fail-{name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,  # Consistent with other managed jobs tests
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_do  # DO does not support spot instances
@pytest.mark.no_vast  # The pipeline.yaml uses other clouds
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.managed_jobs
def test_job_pipeline(generic_cloud: str):
    """Test a job pipeline."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'job_pipeline',
        [
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/pipeline.yaml -y -d',
            # Need to wait for setup and job initialization.
            'sleep 30',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "STARTING\|RUNNING"',
            # `grep -A 4 {name}` finds the job with {name} and the 4 lines
            # after it, i.e. the 4 tasks within the job.
            # `sed -n 2p` gets the second line of the 4 lines, i.e. the first
            # task within the job.
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "STARTING\|RUNNING"',
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "PENDING"',
            f'sky jobs cancel -y -n {name}',
            'sleep 5',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "CANCELLING\|CANCELLED"',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "CANCELLING\|CANCELLED"',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLING\|CANCELLED"',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "CANCELLED"',
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "CANCELLED"',
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_do  # DO does not support spot instances
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_failed_setup(generic_cloud: str):
    """Test managed job with failed setup."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'managed_jobs_failed_setup',
        [
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} -y -d tests/test_yamls/failed_setup.yaml',
            # Make sure the job failed quickly.
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.FAILED_SETUP],
                timeout=365),
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_vast  # Test fails to stay within a single cloud
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_failed_setup(generic_cloud: str):
    """Test managed job with failed setup for a pipeline."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'managed_jobs_pipeline_failed_setup',
        [
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} -y -d tests/test_yamls/failed_setup_pipeline.yaml',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.FAILED_SETUP],
                timeout=600),
            # Make sure the job failed quickly.
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "FAILED_SETUP"',
            # Task 0 should be SUCCEEDED.
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "SUCCEEDED"',
            # Task 1 should be FAILED_SETUP.
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "FAILED_SETUP"',
            # Task 2 should be CANCELLED.
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            # Task 3 should be CANCELLED.
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing managed job recovery ----------


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_aws(aws_config_region):
    """Test managed job recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = smoke_tests_utils.Test(
        'managed_jobs_recovery_aws',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            rf'sky jobs launch --infra aws/{region} --use-spot -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=600),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'aws ec2 terminate-instances --region {region} --instance-ids $('
                 f'aws ec2 describe-instances --region {region} '
                 f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
                 f'--query Reservations[].Instances[].InstanceId '
                 f'--output text)')),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_gcp():
    """Test managed job recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-east4-b'
    query_cmd = (
        f'gcloud compute instances list --filter='
        # `:` means prefix match.
        f'"(labels.ray-cluster-name:{name_on_cloud})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'managed_jobs_recovery_gcp',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            rf'sky jobs launch --infra gcp/*/{zone} -n {name} --use-spot {smoke_tests_utils.LOW_RESOURCE_ARG} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=300),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=terminate_cmd),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_aws(aws_config_region):
    """Test managed job recovery for a pipeline."""
    name = smoke_tests_utils.get_cluster_name()
    user_hash = common_utils.get_user_hash()
    region = aws_config_region
    if region != 'us-east-2':
        pytest.skip('Only run spot pipeline recovery test in us-east-2')
    test = smoke_tests_utils.Test(
        'managed_jobs_pipeline_recovery_aws',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            f'sky jobs launch -n {name} tests/test_yamls/pipeline_aws.yaml -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (
                f'export MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
                'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`; '
                'echo "Managed job id: $MANAGED_JOB_ID"; ' +
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    cmd=(
                        f'aws ec2 terminate-instances --region {region} --instance-ids $('
                        f'aws ec2 describe-instances --region {region} '
                        # TODO(zhwu): fix the name for spot cluster.
                        '--filters Name=tag:ray-cluster-name,Values=*-${MANAGED_JOB_ID}'
                        f'-{user_hash} '
                        f'--query Reservations[].Instances[].InstanceId '
                        '--output text)'),
                    envs={'MANAGED_JOB_ID'})),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        f'sky jobs cancel -y -n {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_gcp():
    """Test managed job recovery for a pipeline."""
    name = smoke_tests_utils.get_cluster_name()
    zone = 'us-east4-b'
    user_hash = common_utils.get_user_hash()
    query_cmd = (
        'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:*-${{MANAGED_JOB_ID}}-{user_hash})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'managed_jobs_pipeline_recovery_gcp',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/pipeline_gcp.yaml -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (f'export MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
             f'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`; ' +
             smoke_tests_utils.run_cloud_cmd_on_cluster(
                 name, cmd=terminate_cmd, envs={'MANAGED_JOB_ID'})),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=240),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        f'sky jobs cancel -y -n {name}; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_do  # DO does not have spot instances
@pytest.mark.no_vast  # Uses other clouds
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_default_resources(generic_cloud: str):
    """Test managed job recovery for default resources."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'managed-spot-recovery-default-resources',
        [
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} --use-spot "sleep 30 && sudo shutdown now && sleep 1000" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[
                    sky.ManagedJobStatus.RUNNING,
                    sky.ManagedJobStatus.RECOVERING
                ],
                timeout=360),
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_aws(aws_config_region):
    """Test managed job recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = smoke_tests_utils.Test(
        'managed_jobs_recovery_multi_node_aws',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            rf'sky jobs launch --infra aws/{region} -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=450),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'aws ec2 terminate-instances --region {region} --instance-ids $('
                 f'aws ec2 describe-instances --region {region} '
                 f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
                 'Name=tag:ray-node-type,Values=worker '
                 f'--query Reservations[].Instances[].InstanceId '
                 '--output text)')),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=560),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_gcp():
    """Test managed job recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-central1-a'
    # Use ':' to match as the cluster name will contain the suffix with job id
    query_cmd = (
        f'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:{name_on_cloud} AND '
        f'labels.ray-node-type=worker)" --zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'managed_jobs_recovery_multi_node_gcp',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            rf'sky jobs launch --infra gcp/*/{zone} -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=terminate_cmd),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=560),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_aws(aws_config_region):
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_2_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-2', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-3', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)

    region = aws_config_region
    test = smoke_tests_utils.Test(
        'managed_jobs_cancellation_aws',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --infra aws/{region} -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot "sleep 1000" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[
                    sky.ManagedJobStatus.STARTING, sky.ManagedJobStatus.RUNNING
                ],
                timeout=95),
            f'sky jobs cancel -y -n {name}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f's=$(aws ec2 describe-instances --region {region} '
                 f'--filters "Name=tag:ray-cluster-name,Values={name_on_cloud}-*" '
                 '--query "Reservations[].Instances[].State[].Name" '
                 '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
                )),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --infra aws/{region} -n {name}-2 {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot tests/test_yamls/test_long_setup.yaml -y -d',
            # The job is set up in the cluster, will shown as RUNNING.
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-2',
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=335),
            f'sky jobs cancel -y -n {name}-2',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-2',
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f's=$(aws ec2 describe-instances --region {region} '
                 f'--filters "Name=tag:ray-cluster-name,Values={name_2_on_cloud}-*" '
                 '--query "Reservations[].Instances[].State[].Name" '
                 '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
                )),
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --infra aws/{region} -n {name}-3 {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot "sleep 1000" -y -d',
            # The job is running in the cluster, will shown as RUNNING.
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-3',
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=335),
            # Terminate the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'aws ec2 terminate-instances --region {region} --instance-ids $('
                 f'aws ec2 describe-instances --region {region} '
                 f'--filters "Name=tag:ray-cluster-name,Values={name_3_on_cloud}-*" '
                 f'--query "Reservations[].Instances[].InstanceId" '
                 '--output text)')),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=f'{name}-3'),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name}-3 | head -n1 | grep "RECOVERING"',
            f'sky jobs cancel -y -n {name}-3',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-3',
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            # The cluster should be terminated (shutting-down) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f's=$(aws ec2 describe-instances --region {region} '
                 f'--filters "Name=tag:ray-cluster-name,Values={name_3_on_cloud}-*" '
                 '--query "Reservations[].Instances[].State[].Name" '
                 '--output text) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "pending|running|stopped|stopping"'
                )),
        ],
        smoke_tests_utils.down_cluster_for_cloud_cmd(name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_gcp():
    name = smoke_tests_utils.get_cluster_name()
    # Reduce the name length further to avoid cluster name to be truncated twice
    # after adding the suffix '-3'.
    name_3 = name.replace('-jobs', '-j') + '-3'
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        name_3, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-west3-b'
    query_state_cmd = (
        'gcloud compute instances list '
        f'--filter="(labels.ray-cluster-name:{name_3_on_cloud})" '
        '--format="value(status)"')
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name:{name_3_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'managed_jobs_cancellation_gcp',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --infra gcp/*/{zone} -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot "sleep 1000" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.STARTING],
                timeout=95),
            f'sky jobs cancel -y -n {name}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --infra gcp/*/{zone} -n {name}-2 {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot tests/test_yamls/test_long_setup.yaml -y -d',
            # The job is set up in the cluster, will shown as RUNNING.
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-2',
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=335),
            f'sky jobs cancel -y -n {name}-2',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}-2',
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --infra gcp/*/{zone} -n {name_3} {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot "sleep 1000" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name_3,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=335),
            # Terminate the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=terminate_cmd),
            smoke_tests_utils.JOB_WAIT_NOT_RUNNING.format(job_name=name_3),
            f'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name_3} | head -n1 | grep "RECOVERING"',
            f'sky jobs cancel -y -n {name_3}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name_3,
                job_status=[sky.ManagedJobStatus.CANCELLED],
                timeout=155),
            # The cluster should be terminated (STOPPING) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f's=$({query_state_cmd}) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "PROVISIONING|STAGING|RUNNING|REPAIRING|TERMINATED|SUSPENDING|SUSPENDED|SUSPENDED"'
                )),
        ],
        smoke_tests_utils.down_cluster_for_cloud_cmd(name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=25 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Uses other clouds
@pytest.mark.no_hyperbolic  # Uses other clouds
@pytest.mark.managed_jobs
def test_managed_jobs_retry_logs(generic_cloud: str):
    """Test managed job retry logs are properly displayed when a task fails."""
    timeout = 7 * 60  # 7 mins
    if generic_cloud == 'azure':
        timeout *= 2
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = 'tests/test_yamls/test_managed_jobs_retry.yaml'
    yaml_config = common_utils.read_yaml_all(yaml_path)
    for task_config in yaml_config:
        task_config['resources'] = task_config.get('resources', {})
        task_config['resources']['cloud'] = generic_cloud

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as yaml_file:
        common_utils.dump_yaml(yaml_file.name, yaml_config)
        yaml_path = yaml_file.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log') as log_file:
            test = smoke_tests_utils.Test(
                'managed_jobs_retry_logs',
                [
                    # TODO(zhwu): we should make the override for generic_cloud
                    # work with multiple stages in pipeline.
                    f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path} -y -d',
                    # TODO(zhwu): Check why the logs does not return immediately
                    # after job status FAILED.
                    f'sky jobs logs -n {name} | tee {log_file.name} ',
                    # First attempt
                    f'cat {log_file.name} | grep "Job started. Streaming logs..."',
                    f'cat {log_file.name} | grep "Job 1 failed"',
                    # Second attempt
                    f'cat {log_file.name} | grep "Job started. Streaming logs..." | wc -l | grep 2',
                    f'cat {log_file.name} | grep "Job 1 failed" | wc -l | grep 2',
                    # Task 2 is not reached
                    f'! cat {log_file.name} | grep "Job 2"',
                ],
                f'sky jobs cancel -y -n {name}',
                env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
                timeout=timeout)
            smoke_tests_utils.run_one_test(test)


# ---------- Testing storage for managed job ----------
@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_do  # DO does not support spot instances
@pytest.mark.no_vast  # Uses other clouds
@pytest.mark.no_nebius  # Nebius does not support spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_storage(generic_cloud: str):
    """Test storage with managed job"""
    timeout = 215
    low_resource_arg = smoke_tests_utils.LOW_RESOURCE_ARG
    name = smoke_tests_utils.get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_job_with_storage.yaml').read_text()
    timestamp = int(time.time())
    storage_name = f'sky-test-{timestamp}'
    output_storage_name = f'sky-test-output-{timestamp}'

    # First, add an initialization for region
    region = None
    region_flag = ''
    region_validation_base_cmd = 'true'
    use_spot = ' --use-spot'
    output_check_cmd = None

    # Also perform region testing for bucket creation to validate if buckets are
    # created in the correct region and correctly mounted in managed jobs.
    # However, we inject this testing only for AWS and GCP since they are the
    # supported object storage providers in SkyPilot.
    if generic_cloud == 'aws':
        region = 'eu-central-1'
        region_flag = f'/{region}'
        region_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.S3, bucket_name=output_storage_name)
        region_validation_base_cmd = f's=$({region_cmd}) && echo "$s" && echo; echo "$s" | grep {region}'
        s3_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        output_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, f'{s3_check_file_count} | grep 1')
        non_persistent_bucket_removed_check_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.S3, storage_name)
        non_persistent_bucket_removed_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name,
            f'{non_persistent_bucket_removed_check_cmd} && exit 1 || true')
    elif generic_cloud == 'gcp':
        region = 'us-central1'
        region_flag = f'/{region}'
        region_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.GCS, bucket_name=output_storage_name)
        region_validation_base_cmd = f'{region_cmd} | grep {region}'
        gcs_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        output_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, f'{gcs_check_file_count} | grep 1')
        non_persistent_bucket_removed_check_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.GCS, storage_name)
        non_persistent_bucket_removed_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name,
            f'{non_persistent_bucket_removed_check_cmd} && exit 1 || true')
    elif generic_cloud == 'azure':
        # Azure instances with smaller than 7G memory can have flaky performance,
        # so we keep the default resource to avoid flakiness.
        low_resource_arg = ""
        region = 'centralus'
        region_flag = f'/{region}'
        storage_account_name = test_mount_and_storage.TestStorageWithCredentials.\
            get_az_storage_account_name(region)
        region_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.AZURE,
            storage_account_name=storage_account_name)
        region_validation_base_cmd = f'{region_cmd} | grep {region}'
        az_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.AZURE,
            output_storage_name,
            'output.txt',
            storage_account_name=storage_account_name)
        output_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, f'{az_check_file_count} | grep 1')
        non_persistent_bucket_removed_check_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.AZURE, storage_name)
        non_persistent_bucket_removed_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name,
            f'{non_persistent_bucket_removed_check_cmd} && exit 1 || true')
        timeout *= 2
    elif generic_cloud == 'kubernetes':
        # With Kubernetes, we don't know which object storage provider is used.
        # Check S3, GCS and Azure if bucket exists in any of them.
        s3_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        s3_output_check_cmd = f'{s3_check_file_count} | grep 1'
        gcs_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        gcs_output_check_cmd = f'{gcs_check_file_count} | grep 1'
        # For Azure, we need to get the storage account name for the region
        storage_account_name = test_mount_and_storage.TestStorageWithCredentials.get_az_storage_account_name(
        )
        az_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.AZURE,
            output_storage_name,
            'output.txt',
            storage_account_name=storage_account_name)
        az_output_check_cmd = f'{az_check_file_count} | grep 1'
        cloud_dependencies_setup_cmd = ' && '.join(
            controller_utils._get_cloud_dependencies_installation_commands(
                controller_utils.Controllers.JOBS_CONTROLLER))
        try_activating_gcp_service_account = (
            f'GOOGLE_APPLICATION_CREDENTIALS={gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH}; '
            'gcloud auth activate-service-account '
            '--key-file=$GOOGLE_APPLICATION_CREDENTIALS '
            '2> /dev/null || true')
        output_check_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, f'{cloud_dependencies_setup_cmd}; '
            f'{try_activating_gcp_service_account}; '
            f'{{ {s3_output_check_cmd} || {gcs_output_check_cmd} || {az_output_check_cmd}; }}'
        )
        use_spot = ' --no-use-spot'
        storage_removed_check_s3_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.S3, storage_name)
        storage_removed_check_gcs_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.GCS, storage_name)
        storage_removed_check_az_cmd = test_mount_and_storage.TestStorageWithCredentials.cli_ls_cmd(
            storage_lib.StoreType.AZURE, storage_name)
        non_persistent_bucket_removed_check_cmd = (
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, f'{{ {storage_removed_check_s3_cmd} && exit 1; }} || '
                f'{{ {storage_removed_check_gcs_cmd} && exit 1; }} || '
                f'{{ {storage_removed_check_az_cmd} && exit 1; }} || true'))
        timeout *= 4

    # Apply universal retry mechanism with 30s timeout for region validation.
    # This is useful for jobs consolidation mode, where the job submission is
    # very fast (don't need to launch a controller VM) and the bucket might not
    # be created yet immediately after the job submission.
    region_validation_cmd_with_retry = region_validation_base_cmd
    region_validation_timeout_for_consolidation = 30
    # Only apply to non-trivial region validation commands.
    if region_validation_base_cmd != 'true':
        if smoke_tests_utils.server_side_is_consolidation_mode():
            region_validation_cmd = (
                'start_time=$SECONDS; '
                'while true; do '
                f'if (( $SECONDS - start_time > {region_validation_timeout_for_consolidation} )); then '
                f'  echo "Timeout after {region_validation_timeout_for_consolidation} seconds waiting for region validation"; exit 1; '
                'fi; '
                f'if {region_validation_base_cmd}; then '
                '  echo "Region validation succeeded"; break; '
                'fi; '
                'echo "Retrying region validation..."; '
                'sleep 5; '
                'done')
        else:
            region_validation_cmd = region_validation_base_cmd
        region_validation_cmd = smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, region_validation_cmd)
    else:
        region_validation_cmd = region_validation_base_cmd

    yaml_str = yaml_str.replace('sky-workdir-zhwu', storage_name)
    yaml_str = yaml_str.replace('sky-output-bucket', output_storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_str)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'managed_jobs_storage',
            [
                *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    generic_cloud, name),
                # Override CPU/memory requirements to relax resource constraints
                # and reduce the chance of out-of-stock
                f'sky jobs launch -n {name}{use_spot} {low_resource_arg} --infra {generic_cloud}{region_flag} {file_path} -y -d',
                region_validation_cmd,  # Check if the bucket is created in the correct region
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[sky.ManagedJobStatus.SUCCEEDED],
                    timeout=timeout),
                # Wait for the job to be cleaned up.
                'sleep 50',
                # Check if file was written to the mounted output bucket
                output_check_cmd,
                non_persistent_bucket_removed_check_cmd,
            ],
            (f'sky jobs cancel -y -n {name}; '
             f'sky storage delete {output_storage_name} -y; '
             f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)} || true'),
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_managed_jobs_intermediate_storage(generic_cloud: str):
    """Test storage with managed job"""
    name = smoke_tests_utils.get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_job_with_storage.yaml').read_text()
    timestamp = int(time.time())
    storage_name = f'sky-test-{timestamp}'
    output_storage_name = f'sky-test-output-{timestamp}'

    yaml_str_user_config = pathlib.Path(
        'tests/test_yamls/use_intermediate_bucket_config.yaml').read_text()
    intermediate_storage_name = f'intermediate-smoke-test-{timestamp}'

    yaml_str = yaml_str.replace('sky-workdir-zhwu', storage_name)
    yaml_str = yaml_str.replace('sky-output-bucket', output_storage_name)
    yaml_str_user_config = re.sub(r'bucket-jobs-[\w\d]+',
                                  intermediate_storage_name,
                                  yaml_str_user_config)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f_user_config:
        f_user_config.write(yaml_str_user_config)
        f_user_config.flush()
        user_config_path = f_user_config.name

        intermediate_bucket_deletion_cmd = f'aws s3 rb s3://{intermediate_storage_name} --force'
        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f_task:
            f_task.write(yaml_str)
            f_task.flush()
            file_path = f_task.name

            test = smoke_tests_utils.Test(
                'managed_jobs_intermediate_storage',
                [
                    smoke_tests_utils.launch_cluster_for_cloud_cmd(
                        generic_cloud, name),
                    *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
                    # Verify command fails with correct error - run only once
                    # In API server, we don't error out if the bucket does not exist, instead we create it.
                    # f'err=$(sky jobs launch -n {name} --infra {generic_cloud} {file_path} -y 2>&1); '
                    # f'ret=$?; if [ $ret -ne 0 ] && echo "$err" | grep -q "StorageBucketCreateError: '
                    # f'Jobs bucket \'{intermediate_storage_name}\' does not exist."; then exit 0; '
                    # f'else exit 1; fi',
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=
                        f'aws s3api create-bucket --bucket {intermediate_storage_name}'
                    ),
                    f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {file_path} -y',
                    # fail because the bucket does not exist
                    smoke_tests_utils.
                    get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                        job_name=name,
                        job_status=[sky.ManagedJobStatus.SUCCEEDED],
                        timeout=95),
                    # check intermediate bucket exists, it won't be deletd if its user specific
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=
                        f'[ $(aws s3api list-buckets --query "Buckets[?contains(Name, \'{intermediate_storage_name}\')].Name" --output text | wc -l) -eq 1 ]'
                    ),
                ],
                (f'sky jobs cancel -y -n {name}; '
                 f'{smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=intermediate_bucket_deletion_cmd)}; '
                 f'sky storage delete {output_storage_name} -y || true; '
                 f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}'),
                env={
                    skypilot_config.ENV_VAR_GLOBAL_CONFIG: user_config_path,
                    constants.SKY_API_SERVER_URL_ENV_VAR:
                        sky.server.common.get_server_url()
                },
                # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
                timeout=20 * 60,
            )
            smoke_tests_utils.run_one_test(test)


# ---------- Testing spot TPU ----------
@pytest.mark.gcp
@pytest.mark.managed_jobs
@pytest.mark.tpu
def test_managed_jobs_tpu():
    """Test managed job on TPU."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-spot-tpu',
        [
            f'sky jobs launch -n {name} --use-spot examples/tpu/tpuvm_mnist.yaml -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.STARTING],
                timeout=95),
            # TPU takes a while to launch
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[
                    sky.ManagedJobStatus.RUNNING, sky.ManagedJobStatus.SUCCEEDED
                ],
                timeout=935),
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env for managed jobs ----------
@pytest.mark.no_vast  # Uses unsatisfiable machines
@pytest.mark.no_hyperbolic  # Uses unsatisfiable machines
@pytest.mark.managed_jobs
def test_managed_jobs_inline_env(generic_cloud: str):
    """Test managed jobs env"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-managed-jobs-inline-env',
        [
            rf'sky jobs launch -n {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --env TEST_ENV="hello world" -- "echo "\$TEST_ENV"; ([[ ! -z \"\$TEST_ENV\" ]] && [[ ! -z \"\${constants.SKYPILOT_NODE_IPS}\" ]] && [[ ! -z \"\${constants.SKYPILOT_NODE_RANK}\" ]] && [[ ! -z \"\${constants.SKYPILOT_NUM_NODES}\" ]]) || exit 1"',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=55),
            f'JOB_ROW=$(sky jobs queue | grep {name} | head -n1) && '
            f'echo "$JOB_ROW" && echo "$JOB_ROW" | grep "SUCCEEDED" && '
            f'JOB_ID=$(echo "$JOB_ROW" | awk \'{{print $1}}\') && '
            f'echo "JOB_ID=$JOB_ID" && '
            # Test that logs are still available after the job finishes.
            'unset SKYPILOT_DEBUG; s=$(sky jobs logs $JOB_ID --refresh) && echo "$s" && echo "$s" | grep "hello world" && '
            # Make sure we skip the unnecessary logs.
            'echo "$s" | head -n1 | grep "Waiting for"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # The test uses other clouds
@pytest.mark.no_hyperbolic  # The test uses other clouds
@pytest.mark.managed_jobs
def test_managed_jobs_logs_sync_down(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-managed-jobs-logs-sync-down',
        [
            f'sky jobs launch -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y examples/managed_job.yaml -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}',
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=335),
            # Example output of `sky jobs logs --controller 1 --sync-down`:
            #   Job 8 logs (controller): ~/sky_logs/sky-2025-01-19-22-34-45-320451
            's=$(SKYPILOT_DEBUG=0 sky jobs logs --controller --sync-down) && echo "$s" && '
            # Parse the log path
            'log_path=$(echo "$s" | grep -E "Job .* logs \\(controller\\): " | '
            'sed -r "s/\\x1B\\[[0-9;]*[JKmsu]//g" | awk -F": " "{print \$2}") && echo "$log_path" && '
            # Check if the log path is a valid path
            'eval "[ -d $log_path ]"',
            # Example output of `sky jobs logs --sync-down`:
            #   Job 8 logs: ~/sky_logs/managed_jobs/sky-2025-01-19-22-34-45-320451
            's=$(SKYPILOT_DEBUG=0 sky jobs logs --sync-down) && echo "$s" && '
            'log_path=$(echo "$s" | grep -E "Job .* logs: " | '
            'sed -r "s/\\x1B\\[[0-9;]*[JKmsu]//g" | awk -F": " "{print \$2}") && echo "$log_path" && '
            # Check if the log path is a valid path
            'eval "[ -d $log_path ]"',
            # Download jobs controller logs with job name
            f's=$(SKYPILOT_DEBUG=0 sky jobs logs --controller --name {name} --sync-down) && echo "$s" && '
            f'log_path=$(echo "$s" | grep -E "Job .* logs \\(controller\\): " | '
            'sed -r "s/\\x1B\\[[0-9;]*[JKmsu]//g" | awk -F": " "{print \$2}" | sed "s|^~/|$HOME/|") && echo "$log_path" && '
            'echo "$log_path" && eval "[ -d $log_path ]" && '
            'cat $(echo "$log_path")/controller.log | grep "Job status: JobStatus.SETTING_UP\|Job status: JobStatus.RUNNING"',
            # Download jobs logs with job name
            f's=$(SKYPILOT_DEBUG=0 sky jobs logs --name {name} --sync-down) && echo "$s" && '
            f'log_path=$(echo "$s" | grep -E "Job .* logs: " | '
            'sed -r "s/\\x1B\\[[0-9;]*[JKmsu]//g" | awk -F": " "{print \$2}" | sed "s|^~/|$HOME/|") && echo "$log_path" && '
            'echo "$log_path" && eval "[ -d $log_path ]" && '
            'cat $(echo "$log_path")/run.log | grep "start counting"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def _get_ha_kill_test(name: str, generic_cloud: str,
                      status: sky.ManagedJobStatus, first_timeout: int,
                      second_timeout: int) -> smoke_tests_utils.Test:
    return smoke_tests_utils.Test(
        f'test-managed-jobs-ha-kill-{status.value.lower()}',
        [
            f'sky jobs launch -n {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} -y examples/managed_job.yaml -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}', job_status=[status], timeout=first_timeout),
            smoke_tests_utils.kill_and_wait_controller('jobs'),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=second_timeout),
            f's=$(sky jobs logs --controller -n {name} --no-follow); echo "$s"; echo "$s" | grep "Job succeeded."',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "SUCCEEDED"',
        ],
        f'sky jobs cancel -y -n {name}',
        env={
            skypilot_config.ENV_VAR_SKYPILOT_CONFIG: 'tests/test_yamls/managed_jobs_ha_config.yaml'
        },
        timeout=20 * 60,
    )


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_ha_kill_running(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = _get_ha_kill_test(
        name,
        generic_cloud,
        sky.ManagedJobStatus.RUNNING,
        first_timeout=200,
        second_timeout=335,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_ha_kill_starting(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = _get_ha_kill_test(
        name,
        generic_cloud,
        sky.ManagedJobStatus.STARTING,
        first_timeout=95,
        second_timeout=600,
    )
    smoke_tests_utils.run_one_test(test)
