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
import io
import os
import pathlib
import re
import tempfile
import textwrap
import time

import jinja2
import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests import test_mount_and_storage

import sky
from sky import jobs
from sky import skypilot_config
from sky.clouds import gcp
from sky.data import storage as storage_lib
from sky.jobs import utils as managed_jobs_utils
from sky.jobs.client import sdk as jobs_sdk
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import yaml_utils


# ---------- Testing managed job ----------
# TODO(zhwu): make the jobs controller on GCP, to avoid parallel test issues
# when the controller being on Azure, which takes a long time for launching
# step.
@pytest.mark.managed_jobs
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers and auto-stop
@pytest.mark.no_shadeform  # Shadeform does not support host controllers
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
@pytest.mark.no_shadeform  # Shadeform does not support host controllers
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
@pytest.mark.no_do  # DO does not support spot instances
@pytest.mark.no_vast  # The pipeline.yaml uses other clouds
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
@pytest.mark.managed_jobs
def test_job_pipeline(generic_cloud: str):
    """Test a job pipeline."""
    name = smoke_tests_utils.get_cluster_name()
    use_spot = True
    if generic_cloud in ('kubernetes', 'slurm'):
        # Kubernetes and Slurm do not support spot instances
        use_spot = False
    # Use Jinja templating to generate the pipeline YAML with the specific cloud
    template_str = pathlib.Path('tests/test_yamls/pipeline.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud=generic_cloud, use_spot=use_spot)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name

        test = smoke_tests_utils.Test(
            'job_pipeline',
            [
                f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {file_path} -y -d',
                # Wait for job to start (event-based instead of fixed sleep)
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING
                    ],
                    timeout=120),
                # `grep -A 4 {name}` finds the job with {name} and the 4 lines
                # after it, i.e. the 4 tasks within the job.
                # `sed -n 2p` gets the second line of the 4 lines, i.e. the first
                # task within the job. Verify the first task is also STARTING or RUNNING.
                rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "STARTING\|RUNNING"',
                f'{smoke_tests_utils.GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "PENDING"',
                f'sky jobs cancel -y -n {name}',
                # Wait for tasks to transition to CANCELLING/CANCELLED with retry logic
                # to avoid flakiness. Use a reasonable timeout (60s) to allow for
                # cancellation to propagate, especially on slower clouds like Kubernetes.
                # Note: task_line corresponds to the line number in grep -A 4 output:
                # line 1 = job header, line 2 = task 0, line 3 = task 1, line 4 = task 2, line 5 = task 3
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=2,
                    expected_status='CANCELLING|CANCELLED',
                    timeout=60),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=3,
                    expected_status='CANCELLING|CANCELLED',
                    timeout=60),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=4,
                    expected_status='CANCELLING|CANCELLED',
                    timeout=60),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=5,
                    expected_status='CANCELLING|CANCELLED',
                    timeout=60),
                # Wait for tasks to fully transition to CANCELLED (event-based instead of fixed sleep)
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=2,
                    expected_status='CANCELLED',
                    timeout=240),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=3,
                    expected_status='CANCELLED',
                    timeout=240),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=4,
                    expected_status='CANCELLED',
                    timeout=240),
                smoke_tests_utils.get_cmd_wait_until_pipeline_task_status(
                    job_name=name,
                    task_line=5,
                    expected_status='CANCELLED',
                    timeout=240),
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
@pytest.mark.no_vast  # Test fails to stay within a single cloud
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
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
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
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


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_kubernetes_multinode():
    """Test managed job recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    terminate_head_cmd = (
        f'kubectl get pods --no-headers -o custom-columns=":metadata.name" | '
        f'grep -- "{name_on_cloud}-[0-9]*-{common_utils.get_user_hash()}-head" | '
        f'xargs kubectl delete pod')
    terminate_worker_cmd = (
        f'kubectl get pods --no-headers -o custom-columns=":metadata.name" | '
        f'grep -- "{name_on_cloud}-[0-9]*-{common_utils.get_user_hash()}-worker" | '
        f'xargs kubectl delete pod')
    test = smoke_tests_utils.Test(
        'managed_jobs_recovery_kubernetes',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            rf'sky jobs launch --infra kubernetes -n {name} --num-nodes 2 {smoke_tests_utils.LOW_RESOURCE_ARG} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=300),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name,
                                                       cmd=terminate_head_cmd),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RECOVERING],
                # This involves interval, job status check and cluster status
                # check, but should be significantly shorter than the timeout of
                # a transient error retries, as the controller should discover
                # the cluster termination immediately.
                timeout=managed_jobs_utils.JOB_STATUS_CHECK_GAP_SECONDS * 3),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, cmd=terminate_worker_cmd),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.RECOVERING],
                timeout=managed_jobs_utils.JOB_STATUS_CHECK_GAP_SECONDS * 3),
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
@pytest.mark.no_do  # DO does not have spot instances
@pytest.mark.no_vast  # Uses other clouds
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_default_resources(generic_cloud: str):
    """Test managed job recovery for default resources."""
    name = smoke_tests_utils.get_cluster_name()
    use_spot_arg = "--use-spot"
    if generic_cloud in ('kubernetes', 'slurm'):
        # Kubernetes and Slurm do not support spot instances
        use_spot_arg = ""
    test = smoke_tests_utils.Test(
        'managed-spot-recovery-default-resources',
        [
            f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {use_spot_arg} "sleep 30 && sudo shutdown now && sleep 1000" -y -d',
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
@pytest.mark.no_shadeform  # Uses other clouds
@pytest.mark.managed_jobs
def test_managed_jobs_retry_logs(generic_cloud: str):
    """Test managed job retry logs are properly displayed when a task fails."""
    timeout = 7 * 60  # 7 mins
    if generic_cloud == 'azure':
        timeout *= 2
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = 'tests/test_yamls/test_managed_jobs_retry.yaml'
    yaml_config = yaml_utils.read_yaml_all(yaml_path)
    for task_config in yaml_config:
        task_config['resources'] = task_config.get('resources', {})
        task_config['resources']['cloud'] = generic_cloud

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as yaml_file:
        yaml_utils.dump_yaml(yaml_file.name, yaml_config)
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
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
@pytest.mark.managed_jobs
@pytest.mark.no_dependency  # Storage tests required full dependency installed
def test_managed_jobs_storage(generic_cloud: str):
    """Test storage with managed job"""
    timeout = 500
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
        region = 'us-east-2'
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
    elif generic_cloud in ('kubernetes', 'slurm'):
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
        nebius_check_file_count = test_mount_and_storage.TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.NEBIUS, output_storage_name, 'output.txt')
        nebius_output_check_cmd = f'{nebius_check_file_count} | grep 1'
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
            f'{{ {s3_output_check_cmd} || {gcs_output_check_cmd} || {az_output_check_cmd} || {nebius_output_check_cmd}; }}'
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
@pytest.mark.skip(reason='We are having trouble getting TPUs in GCP.')
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
            f'JOB_ROW=$(sky jobs queue -v | grep {name} | head -n1) && '
            f'echo "$JOB_ROW" && echo "$JOB_ROW" | grep -E "DONE|ALIVE" | grep "SUCCEEDED" && '
            f'JOB_ID=$(echo "$JOB_ROW" | awk \'{{print $1}}\') && '
            f'echo "JOB_ID=$JOB_ID" && '
            # Test that logs are still available after the job finishes.
            'unset SKYPILOT_DEBUG; s=$(sky jobs logs $JOB_ID --refresh) && echo "$s" && echo "$s" | grep "hello world" && '
            # Make sure we skip the unnecessary logs.
            'echo "$s" | head -n2 | grep "Waiting for"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # The test uses other clouds
@pytest.mark.no_hyperbolic  # The test uses other clouds
@pytest.mark.no_shadeform  # The test uses other clouds
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


# Only run this test on Kubernetes since this test relies on kubernetes.pod_config
@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_env_isolation(generic_cloud: str):
    """Test that the job controller execution env of jobs are isolated."""
    base_name = smoke_tests_utils.get_cluster_name()
    for i in range(2):
        name = f'{base_name}-{i}'
        # We want to verify job controller isolates the skypilot config for each job,
        # kubernetes.pod_config is the easiest way to verify the correct skypilot config
        # is used in job controller. We assume the job controller is cloud agnostic, thus
        # this case will also cover the same case for other clouds.
        test = smoke_tests_utils.Test(
            'test-managed-jobs-env-isolation',
            [
                # Sleep 60 to workaround the issue that SUCCEED job cannot be tailed by name
                f'sky jobs launch -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y -d \'echo "$TEST_POD_ENV"; sleep 60\'',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}',
                    job_status=[sky.ManagedJobStatus.RUNNING],
                    timeout=600
                    if smoke_tests_utils.is_remote_server_test() else 80),
                f'sky jobs logs -n {name} --no-follow | grep "my name is {name}"',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=f'{name}',
                    job_status=[sky.ManagedJobStatus.SUCCEEDED],
                    timeout=600
                    if smoke_tests_utils.is_remote_server_test() else 80),
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=20 * 60,
            config_dict={
                'kubernetes': {
                    'pod_config': {
                        'spec': {
                            'containers': [{
                                'env': [{
                                    'name': 'TEST_POD_ENV',
                                    'value': f'my name is {name}'
                                }]
                            }]
                        }
                    }
                }
            })
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
@pytest.mark.managed_jobs
def test_managed_jobs_config_labels_isolation(generic_cloud: str, request):
    supported_clouds = {'aws', 'gcp', 'kubernetes'}
    if generic_cloud not in supported_clouds:
        pytest.skip(
            f'Unsupported cloud {generic_cloud} for label isolation test.')

    name = smoke_tests_utils.get_cluster_name()
    job_with_config = f'{name}-cfg'
    job_without_config = f'{name}-plain'
    label_key = 'skypilot-smoke-label'
    label_value = f'{name}-value'

    if generic_cloud == 'aws':
        region = request.getfixturevalue('aws_config_region')
        infra_arg = f'--infra aws/{region}'
        config_dict = {'aws': {'labels': {label_key: label_value}}}
        get_instance_cmd = (
            f'aws ec2 describe-instances --region {region} '
            f'--filters Name=tag:{label_key},Values={label_value} '
            'Name=instance-state-name,Values=running '
            '--query "Reservations[].Instances[].Tags[?Key==\'Name\'].Value" '
            '--output text | grep -v "sky-jobs-controller"')
        presence_cmd = (f'OUTPUT=$({get_instance_cmd}); '
                        'echo "$OUTPUT"; '
                        'test -n "$OUTPUT"')
        absence_cmd = (f'OUTPUT=$({get_instance_cmd}); '
                       'echo "$OUTPUT"; '
                       'test -z "$OUTPUT"')
    elif generic_cloud == 'gcp':
        infra_arg = '--infra gcp'
        config_dict = {'gcp': {'labels': {label_key: label_value}}}
        get_instance_cmd = (
            f'gcloud compute instances list '
            f'--filter="labels.{label_key}={label_value}" '
            '--format="value(name,zone)" | grep -v "sky-jobs-controller"')
        presence_cmd = (f'INSTANCES=$({get_instance_cmd}); '
                        'echo "$INSTANCES"; '
                        'test -n "$INSTANCES"')
        absence_cmd = (f'INSTANCES=$({get_instance_cmd}); '
                       'echo "$INSTANCES"; '
                       'test -z "$INSTANCES"')
    else:  # kubernetes
        infra_arg = '--infra kubernetes'
        config_dict = {
            'kubernetes': {
                'custom_metadata': {
                    'labels': {
                        label_key: label_value,
                    }
                }
            }
        }
        get_instance_cmd = (
            f'kubectl get pods -A -l {label_key}={label_value} '
            '--no-headers | grep -v "sky-jobs-controller" || true')
        presence_cmd = (f'PODS=$({get_instance_cmd}); '
                        'echo "$PODS"; '
                        'test -n "$PODS"')
        absence_cmd = (f'PODS=$({get_instance_cmd}); '
                       'echo "$PODS"; '
                       'test -z "$PODS"')

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as config_file:
        config_file.write(yaml_utils.dump_yaml_str(config_dict))
        config_file.flush()

        commands = [
            smoke_tests_utils.with_config(
                f'sky jobs launch -n {job_with_config} {infra_arg} '
                f'{smoke_tests_utils.LOW_RESOURCE_ARG} "sleep 180" -y -d',
                config_file.name),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=job_with_config,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=600),
            f'echo "Checking label presence for {job_with_config}"',
            presence_cmd,
            f'sky jobs cancel -y -n {job_with_config}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=job_with_config,
                job_status=[
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.SUCCEEDED
                ],
                timeout=240),
        ]

        commands.extend([
            f'sky jobs launch -n {job_without_config} {infra_arg} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} "sleep 180" -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=job_without_config,
                job_status=[sky.ManagedJobStatus.RUNNING],
                timeout=600),
            f'echo "Checking label absence for {job_without_config}"',
            absence_cmd,
            f'sky jobs cancel -y -n {job_without_config}',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=job_without_config,
                job_status=[
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.SUCCEEDED
                ],
                timeout=240),
        ])

        test = smoke_tests_utils.Test(
            'managed_jobs_config_labels_isolation',
            commands,
            teardown=(f'sky jobs cancel -y -n {job_with_config}; '
                      f'sky jobs cancel -y -n {job_without_config}'),
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=25 * 60,
        )
        smoke_tests_utils.run_one_test(test)


def _get_ha_kill_test(name: str, generic_cloud: str,
                      status: sky.ManagedJobStatus, first_timeout: int,
                      second_timeout: int) -> smoke_tests_utils.Test:
    skypilot_config_path = 'tests/test_yamls/managed_jobs_ha_config.yaml'

    pytest_config_file_override = smoke_tests_utils.pytest_config_file_override(
    )
    if pytest_config_file_override is not None:
        with open(pytest_config_file_override, 'r') as f:
            base_config = f.read()
        with open(skypilot_config_path, 'r') as f:
            ha_config = f.read()
        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                         delete=False) as f:
            f.write(base_config)
            f.write(ha_config)
            f.flush()
            skypilot_config_path = f.name

    return smoke_tests_utils.Test(
        f'test-managed-jobs-ha-kill-{status.value.lower()}',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd(generic_cloud, name),
            f'sky jobs launch -n {name} --infra {generic_cloud} '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} -y examples/managed_job.yaml -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}', job_status=[status], timeout=first_timeout),
            smoke_tests_utils.kill_and_wait_controller(name, 'jobs'),
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=second_timeout),
            f's=$(sky jobs logs --controller -n {name} --no-follow); echo "$s"; echo "$s" | grep "Job succeeded."',
            rf'{smoke_tests_utils.GET_JOB_QUEUE} | grep {name} | head -n1 | grep "SUCCEEDED"',
        ],
        f'sky jobs cancel -y -n {name}',
        env={skypilot_config.ENV_VAR_SKYPILOT_CONFIG: skypilot_config_path},
        timeout=20 * 60,
    )


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_ha_kill_running(generic_cloud: str):
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip(
            'Skipping HA test in non-docker remote api server environment as '
            'controller might be managed by different user/test agents')

    name = smoke_tests_utils.get_cluster_name()
    test = _get_ha_kill_test(
        name,
        generic_cloud,
        sky.ManagedJobStatus.RUNNING,
        first_timeout=200,
        second_timeout=600,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
def test_managed_jobs_ha_kill_starting(generic_cloud: str):
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip(
            'Skipping HA test in non-docker remote api server environment as '
            'controller might be managed by different user/test agents')
    name = smoke_tests_utils.get_cluster_name()
    test = _get_ha_kill_test(
        name,
        generic_cloud,
        sky.ManagedJobStatus.STARTING,
        first_timeout=95,
        second_timeout=600,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.parametrize(
    'bucket_name',
    [
        # Generate a unique bucket name in the test
        # Fails with:
        # [sky.exceptions.StorageSpecError] Attempted to mount a non-sky managed bucket '...' without specifying the storage source. Bucket '...' already exists.
        None,
        # Too short
        # Fails with:
        # [sky.exceptions.StorageNameError] Invalid store name: name ab must be between 3 (min) and 63 (max) characters long.
        'ab',
        # Access denied (as of time of writing, this bucket happens to exist on both S3 and GCS and is private)
        # Fails with:
        # [sky.exceptions.StorageBucketGetError] Failed to access existing bucket 'not-my-bucket'. This is likely because it is a private bucket you do not have access to.
        'not-my-bucket'
    ])
def test_managed_jobs_failed_precheck_storage_spec_error(
        generic_cloud: str, aws_config_region, bucket_name):
    """Test that jobs fail with FAILED_PRECHECKS instead of stuck in PENDING."""
    supported_clouds = {'aws', 'gcp'}
    if generic_cloud not in supported_clouds:
        pytest.skip(
            f'Unsupported cloud {generic_cloud} for storage spec error test.')

    name = smoke_tests_utils.get_cluster_name()
    create_bucket = False
    if bucket_name is None:
        bucket_name = f'{name}-{int(time.time())}'
        create_bucket = True

    if generic_cloud == 'aws':
        region = aws_config_region
        infra_arg = f'--infra aws/{region}'
        store = 's3'
        create_bucket_cmd = (
            f'aws s3api create-bucket --bucket {bucket_name} --region {region}'
            + ('' if region == 'us-east-1' else
               f' --create-bucket-configuration LocationConstraint={region}'))
        delete_bucket_cmd = f'aws s3 rb s3://{bucket_name} --force'
    elif generic_cloud == 'gcp':
        infra_arg = '--infra gcp'
        store = 'gcs'
        create_bucket_cmd = f'gsutil mb gs://{bucket_name}'
        delete_bucket_cmd = f'gsutil rm -r gs://{bucket_name}'

    template_str = pathlib.Path(
        'tests/test_yamls/test_storage_mount.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(bucket_name=bucket_name, store=store)

    config_dict = {
        'jobs': {
            'force_disable_cloud_bucket': True,
            'controller': {
                'resources': {
                    'cpus': '2+',
                    'memory': '4+'
                }
            }
        }
    }

    with tempfile.NamedTemporaryFile(suffix='.yaml',
                                      mode='w') as f_config, \
        tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        yaml_utils.dump_yaml(f_config.name, config_dict)
        f_config.flush()

        f.write(content)
        f.flush()
        file_path = f.name

        base_commands = [
            f'sky jobs launch -n {name} {infra_arg} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.FAILED_PRECHECKS],
                timeout=300),
            f'logs=$(sky jobs logs --controller -n {name} --no-follow); echo "$logs"; echo "$logs" | grep -i "Storage.*Error"',
        ]

        commands = base_commands
        if create_bucket:
            create_bucket_commands = [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    generic_cloud, name),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, cmd=create_bucket_cmd),
            ]
            commands = create_bucket_commands + base_commands

        teardown_commands = [f'sky jobs cancel -y -n {name}']
        if create_bucket:
            teardown_commands.extend([
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, cmd=delete_bucket_cmd),
                smoke_tests_utils.down_cluster_for_cloud_cmd(name)
            ])
        teardown = '; '.join(teardown_commands)

        test = smoke_tests_utils.Test(
            'managed_jobs_failed_precheck_storage_spec_error',
            commands,
            teardown,
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: f_config.name,
            },
            timeout=15 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # Need an isolated API server for this test case
@pytest.mark.managed_jobs
@pytest.mark.no_dependency  # restart api server requires full dependency installed
def test_managed_jobs_logs_gc(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    log_cleaned_hint = 'log has been cleaned'

    def wait_logs_gced(controller: bool = False):
        now = time.time()
        while time.time() - now < 300:
            output = io.StringIO()
            # Just tail the latest log since we assum isolated server
            jobs_sdk.tail_logs(follow=False,
                               controller=controller,
                               output_stream=output)
            if log_cleaned_hint in output.getvalue():
                return
            yield f'Waiting for logs to be garbage collected, controller: {controller}'
            time.sleep(15)
        raise RuntimeError('Tiemout wait logs get gced')

    test = smoke_tests_utils.Test(
        name='test-managed-jobs-logs-gc',
        config_dict={
            'jobs': {
                'controller': {
                    # GC immediately for testing
                    'controller_logs_gc_retention_hours': 0,
                    'task_logs_gc_retention_hours': 0,
                }
            }
        },
        commands=[
            # Restart the API server to apply the server-side config
            'sky api stop && sky api start',
            f'sky jobs launch -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} -y "echo hello" -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=f'{name}',
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=600),
            lambda: wait_logs_gced(controller=False),
            lambda: wait_logs_gced(controller=True),
            # jobs logs should still work, but show cleaned hint
            f's=$(sky jobs logs) && echo "$s" && echo "$s" | grep "{log_cleaned_hint}" && echo "$s" | grep "SUCCEEDED"',
            f's=$(sky jobs logs --controller) && echo "$s" && echo "$s" | grep "{log_cleaned_hint}" && echo "$s" | grep "SUCCEEDED"',
            # sync down should still work
            'sky jobs logs --sync-down'
        ],
        # Stop the API server so that it doesn't refer to the deleted config
        # file after the test exits
        teardown=f'sky jobs cancel -y -n {name}; sky api stop',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
def test_managed_jobs_exit_code_recovery(generic_cloud: str):
    """Test managed job recovery based on specific exit codes."""
    name = smoke_tests_utils.get_cluster_name()

    # Create YAML with exit code recovery
    yaml_content = textwrap.dedent("""\
        resources:
          cpus: 2+
          job_recovery:
            max_restarts_on_errors: 0
            recover_on_exit_codes: [29]

        run: |
          echo "Job starting, will exit with code 29 after 30 seconds"
          sleep 30
          echo "Exiting with code 29 to trigger recovery"
          exit 29
        """)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'managed_jobs_exit_code_recovery',
            [
                f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {yaml_path} -y -d',
                # Wait for job to start running
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.RUNNING,
                    ],
                    timeout=300),
                # Wait a bit for the job to fail and start recovery
                'sleep 60',
                # Check that recovery count is greater than 0
                # Recovery count is NF-2 (third column from the end)
                f'for i in {{1..20}}; do '
                f'  RECOVERY_COUNT=$(sky jobs queue | grep {name} | head -n1 | awk \'{{print $(NF-2)}}\'); '
                f'  echo "Recovery count: $RECOVERY_COUNT"; '
                f'  if [ "$RECOVERY_COUNT" != "-" ] && [ "$RECOVERY_COUNT" -gt 0 ]; then '
                f'    echo "Recovery count is greater than 0: $RECOVERY_COUNT"; '
                f'    exit 0; '
                f'  fi; '
                f'  echo "Waiting for recovery count to increase (attempt $i/20)..."; '
                f'  sleep 15; '
                f'done; '
                f'echo "Recovery count did not increase after 5 minutes"; '
                f'exit 1',
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=25 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
def test_managed_jobs_exit_code_recovery_multinode(generic_cloud: str):
    """Test managed job recovery based on exit codes in multi-node jobs."""
    name = smoke_tests_utils.get_cluster_name()

    # Create YAML with exit code recovery for multi-node job
    yaml_content = textwrap.dedent("""\
        num_nodes: 2

        resources:
          cpus: 2+
          job_recovery:
            max_restarts_on_errors: 0
            recover_on_exit_codes: [29]

        run: |
          # Node 1 sleeps for 30 seconds then fails.
          # Node 0 sleeps for 30 seconds then succeeds.
          echo "Node: $SKYPILOT_NODE_RANK starting"
          if [ "$SKYPILOT_NODE_RANK" == "1" ]; then
            sleep 30
            echo "Node 1 failing"
            exit 29
          fi
          # Node 0 sleeps for 30 seconds.
          if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
            sleep 30
            echo "Node 0 finishing"
            exit 0
          fi
        """)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'managed_jobs_exit_code_recovery_multinode',
            [
                f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {yaml_path} -y -d',
                # Wait for job to start running
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.RUNNING,
                    ],
                    timeout=300),
                # Wait a bit for the job to fail and start recovery
                'sleep 60',
                # Check that recovery count is greater than 0
                # Recovery count is NF-2 (third column from the end)
                f'for i in {{1..20}}; do '
                f'  RECOVERY_COUNT=$(sky jobs queue | grep {name} | head -n1 | awk \'{{print $(NF-2)}}\'); '
                f'  echo "Recovery count: $RECOVERY_COUNT"; '
                f'  if [ "$RECOVERY_COUNT" != "-" ] && [ "$RECOVERY_COUNT" -gt 0 ]; then '
                f'    echo "Recovery count is greater than 0: $RECOVERY_COUNT"; '
                f'    exit 0; '
                f'  fi; '
                f'  echo "Waiting for recovery count to increase (attempt $i/20)..."; '
                f'  sleep 15; '
                f'done; '
                f'echo "Recovery count did not increase after 5 minutes"; '
                f'exit 1',
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=25 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
def test_managed_jobs_exit_code_recovery_single(generic_cloud: str):
    """Test managed job recovery with a single exit code (not a list)."""
    name = smoke_tests_utils.get_cluster_name()

    # Create YAML with single exit code (not a list)
    yaml_content = textwrap.dedent("""\
        resources:
          cpus: 2+
          job_recovery:
            max_restarts_on_errors: 0
            recover_on_exit_codes: 31

        run: |
          echo "Job starting, will exit with code 31 after 30 seconds"
          sleep 30
          echo "Exiting with code 31 to trigger recovery"
          exit 31
        """)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'managed_jobs_exit_code_recovery_single',
            [
                f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {yaml_path} -y -d',
                # Wait for job to start running
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.RUNNING,
                    ],
                    timeout=300),
                # Wait a bit for the job to fail and start recovery
                'sleep 60',
                # Check that recovery count is greater than 0
                # Recovery count is NF-2 (third column from the end)
                f'for i in {{1..20}}; do '
                f'  RECOVERY_COUNT=$(sky jobs queue | grep {name} | head -n1 | awk \'{{print $(NF-2)}}\'); '
                f'  echo "Recovery count: $RECOVERY_COUNT"; '
                f'  if [ "$RECOVERY_COUNT" != "-" ] && [ "$RECOVERY_COUNT" -gt 0 ]; then '
                f'    echo "Recovery count is greater than 0: $RECOVERY_COUNT"; '
                f'    exit 0; '
                f'  fi; '
                f'  echo "Waiting for recovery count to increase (attempt $i/20)..."; '
                f'  sleep 15; '
                f'done; '
                f'echo "Recovery count did not increase after 5 minutes"; '
                f'exit 1',
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=25 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
@pytest.mark.managed_jobs
def test_managed_job_labels_in_queue(generic_cloud: str):
    """Test that labels in managed job YAML are stored and returned in queue."""
    from sky.client import sdk

    name = smoke_tests_utils.get_cluster_name()
    expected_labels = {'test-label': 'test-value', 'project': 'smoke-test'}

    def check_labels_in_queue():
        """Check that labels are present in the job queue."""
        # Get the job queue using SDK
        queue_request_id = jobs_sdk.queue_v2(refresh=False, all_users=True)
        queue_records = sdk.stream_and_get(queue_request_id)

        # Parse the queue response
        if isinstance(queue_records, tuple):
            jobs, _, _, _ = queue_records
        else:
            jobs = queue_records

        # Find our job in the queue
        job_record = None
        for job in jobs:
            if job.get('job_name') == name:
                job_record = job
                break

        if job_record is None:
            yield f'Job {name} not found in queue'
            return

        if 'labels' not in job_record:
            yield 'labels field missing from job record'
            return

        if job_record['labels'] is None:
            yield 'labels field is None'
            return

        if job_record['labels'] != expected_labels:
            yield (f"Expected labels {expected_labels}, "
                   f"got {job_record['labels']}")
            return

        # Success - labels are correct
        return

    # Create YAML with labels
    yaml_content = textwrap.dedent("""\
        resources:
          cpus: 2+
          labels:
            test-label: test-value
            project: smoke-test

        run: |
          echo "Hello from labeled job"
          sleep 10000
        """)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'managed_job_labels_in_queue',
            [
                f'sky jobs launch -n {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path} -y -d',
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[
                        sky.ManagedJobStatus.STARTING,
                        sky.ManagedJobStatus.RUNNING,
                        sky.ManagedJobStatus.SUCCEEDED,
                    ],
                    timeout=120),
                lambda: check_labels_in_queue(),
            ],
            teardown=f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server
@pytest.mark.no_dependency
@pytest.mark.kubernetes
def test_large_production_performance(request):
    if not smoke_tests_utils.is_in_buildkite_env():
        pytest.skip('Skipping test: requires db modification, run only in '
                    'Buildkite.')

    test = smoke_tests_utils.Test(
        name='test-large-production-performance',
        commands=[
            f'bash tests/load_tests/db_scale_tests/test_large_production_performance.sh --postgres --restart-api-server',
        ],
        timeout=30 * 60,  # 30 minutes for data injection and testing
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
def test_managed_jobs_instance_links(generic_cloud: str):
    """Test that instance links are auto-generated for managed jobs.

    This test verifies that when a managed job runs on AWS, GCP, or Azure,
    the instance links are automatically populated in the job record.
    """
    # Only run on clouds that support instance links
    if generic_cloud not in ('aws', 'gcp', 'azure'):
        pytest.skip(f'Instance links not supported on {generic_cloud}')

    name = smoke_tests_utils.get_cluster_name()

    # Simple job that runs for a bit
    yaml_content = textwrap.dedent("""\
        resources:
          cpus: 2+

        run: |
          echo "Job started"
          sleep 60
          echo "Job finished"
        """)

    # Load Jinja template to poll for instance links using Python SDK
    template_path = pathlib.Path('tests/test_yamls/test_instance_links.py.j2')
    check_links_template = jinja2.Template(template_path.read_text())
    check_script = check_links_template.render(
        name=name,
        generic_cloud=generic_cloud,
    )

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f, \
         tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as script_f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

        # Write rendered script to temp file
        script_f.write(check_script)
        script_f.flush()
        check_links_cmd = f'python3 {script_f.name}'

        test = smoke_tests_utils.Test(
            'managed_jobs_instance_links',
            [
                f'sky jobs launch -n {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} {yaml_path} -y -d',
                # Wait for job to start running
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[sky.ManagedJobStatus.RUNNING],
                    timeout=300),
                # Check for instance links
                check_links_cmd,
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=15 * 60,
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Testing JobGroups ----------


def _render_job_group_yaml(yaml_template_path: str, name: str, cloud: str,
                           **kwargs) -> str:
    """Render a JobGroup YAML template with name, cloud, and extra variables."""
    with open(yaml_template_path, 'r') as f:
        template_content = f.read()

    template = jinja2.Template(template_content)
    rendered = template.render(name=name, cloud=cloud, **kwargs)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as f:
        f.write(rendered)
        f.flush()
        return f.name


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
def test_job_group_basic(generic_cloud: str):
    """Test basic JobGroup with 2 parallel jobs."""
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _render_job_group_yaml('tests/test_job_groups/smoke_basic.yaml',
                                       name, generic_cloud)

    test = smoke_tests_utils.Test(
        'job_group_basic',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=360),
            f'sky jobs queue | grep {name} | grep SUCCEEDED',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
def test_job_group_networking(generic_cloud: str):
    """Test JobGroup cross-job networking via hostname resolution."""
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _render_job_group_yaml(
        'tests/test_job_groups/smoke_networking.yaml', name, generic_cloud)

    # NOTE: We use job ID instead of `-n {name}` for `sky jobs logs` because
    # `sky jobs logs -n <name>` only works for running (non-terminal) jobs.
    # For completed jobs, we need to use the job ID directly.
    get_job_id_cmd = (f'sky jobs queue | grep {name} | head -1 | '
                      f'awk \'{{print $1}}\'')
    test = smoke_tests_utils.Test(
        'job_group_networking',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=360),
            f'sky jobs logs $({get_job_id_cmd}) --no-follow | '
            f'grep "SUCCESS: Connected to server"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
@pytest.mark.parametrize(
    'image_id',
    [
        # Ubuntu base image - no sudo installed by default
        'docker:ubuntu:22.04',
        # Miniconda image - commonly used, has Python, no sudo
        'docker:continuumio/miniconda3:24.1.2-0',
    ])
def test_job_group_networking_custom_image(generic_cloud: str, image_id: str):
    """Test JobGroup networking with custom images that have no sudo installed.

    This tests the fix for containers running as root but without sudo binary.
    The DNS updater script must handle this case by aliasing sudo to empty
    when running as root (using ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD).
    """
    # Include image name in cluster name for uniqueness across parametrized runs
    image_suffix = image_id.split(':')[-1].replace('.', '-')[:8]
    name = smoke_tests_utils.get_cluster_name() + f'-{image_suffix}'
    yaml_path = _render_job_group_yaml(
        'tests/test_job_groups/smoke_networking_custom_image.yaml',
        name,
        generic_cloud,
        image_id=image_id)

    get_job_id_cmd = (f'sky jobs queue | grep {name} | head -1 | '
                      f'awk \'{{print $1}}\'')
    test = smoke_tests_utils.Test(
        f'job_group_networking_custom_image_{image_suffix}',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=360),
            # Verify the client connected successfully (proves networking worked)
            f'sky jobs logs $({get_job_id_cmd}) --no-follow | '
            f'grep "SUCCESS: Connected to server on custom image without sudo"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
def test_job_group_rl_architecture(generic_cloud: str):
    """Test JobGroup with RL-style heterogeneous architecture (4 components)."""
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _render_job_group_yaml(
        'tests/test_job_groups/smoke_rl_architecture.yaml', name, generic_cloud)

    test = smoke_tests_utils.Test(
        'job_group_rl_architecture',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=600),
            f'sky jobs queue | grep {name} | grep SUCCEEDED',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
def test_job_group_task_logs(generic_cloud: str):
    """Test task-specific log viewing for JobGroups."""
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _render_job_group_yaml('tests/test_job_groups/smoke_basic.yaml',
                                       name, generic_cloud)

    get_job_id_cmd = (f'sky jobs queue | grep {name} | head -1 | '
                      f'awk \'{{print $1}}\'')
    test = smoke_tests_utils.Test(
        'job_group_task_logs',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=360),
            # Test default behavior - should show all tasks
            f'sky jobs logs $({get_job_id_cmd}) --no-follow | grep "Job A" && '
            f'sky jobs logs $({get_job_id_cmd}) --no-follow | grep "Job B"',
            # Test viewing logs by task ID - should only show job-a
            f'sky jobs logs $({get_job_id_cmd}) 0 --no-follow | '
            f'grep "Job A" && ! sky jobs logs $({get_job_id_cmd}) 0 '
            f'--no-follow | grep "Job B"',
            # Test viewing logs by task name - should only show job-b
            f'sky jobs logs $({get_job_id_cmd}) job-b --no-follow | '
            f'grep "Job B" && ! sky jobs logs $({get_job_id_cmd}) job-b '
            f'--no-follow | grep "Job A"',
            # Test invalid task ID/name - should show error
            f'sky jobs logs $({get_job_id_cmd}) 999 --no-follow 2>&1 | '
            f'grep "No task found matching"',
            f'sky jobs logs $({get_job_id_cmd}) nonexistent --no-follow 2>&1 | '
            f'grep "No task found matching"',
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.kubernetes
def test_job_group_task_logs_sdk(generic_cloud: str):
    """Test SDK task filtering with typed task parameter (int vs str).

    This test verifies that the SDK correctly handles:
    - task=int filters by task_id
    - task=str filters by task_name
    - Invalid task values return appropriate errors
    """
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _render_job_group_yaml('tests/test_job_groups/smoke_basic.yaml',
                                       name, generic_cloud)

    def sdk_task_filter_test():
        # Get job_id for the launched job
        queue_request_id = jobs_sdk.queue_v2(refresh=False)
        queue_records = sky.stream_and_get(queue_request_id)
        # Parse the queue response (queue_v2 returns a tuple)
        if isinstance(queue_records, tuple):
            jobs_list, _, _, _ = queue_records
        else:
            jobs_list = queue_records
        job_id = None
        for job in jobs_list:
            if job.get('job_name') == name:
                job_id = job.get('job_id')
                break
        assert job_id is not None, f'Job {name} not found in queue'

        # Test 1: task=int(0) should filter by task_id and show only job-a
        output = io.StringIO()
        jobs_sdk.tail_logs(job_id=job_id,
                           follow=False,
                           task=0,
                           output_stream=output)
        content = output.getvalue()
        assert 'Job A' in content, f'Expected "Job A" in output for task=0'
        assert 'Job B' not in content, f'Unexpected "Job B" in output for task=0'

        # Test 2: task=str('job-b') should filter by task_name and show only job-b
        output = io.StringIO()
        jobs_sdk.tail_logs(job_id=job_id,
                           follow=False,
                           task='job-b',
                           output_stream=output)
        content = output.getvalue()
        assert 'Job B' in content, f'Expected "Job B" in output for task="job-b"'
        assert 'Job A' not in content, f'Unexpected "Job A" for task="job-b"'

        # Test 3: task=int(999) should fail (non-existent task_id)
        output = io.StringIO()
        jobs_sdk.tail_logs(job_id=job_id,
                           follow=False,
                           task=999,
                           output_stream=output)
        content = output.getvalue()
        assert 'No task found matching 999' in content, (
            f'Expected error for task=999, got: {content}')

        # Test 4: task=str('nonexistent') should fail (non-existent task_name)
        output = io.StringIO()
        jobs_sdk.tail_logs(job_id=job_id,
                           follow=False,
                           task='nonexistent',
                           output_stream=output)
        content = output.getvalue()
        assert 'No task found matching' in content, (
            f'Expected error for task="nonexistent", got: {content}')

    test = smoke_tests_utils.Test(
        'job_group_task_logs_sdk',
        [
            f'sky jobs launch {yaml_path} -y -d',
            smoke_tests_utils.
            get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=name,
                job_status=[sky.ManagedJobStatus.SUCCEEDED],
                timeout=360),
            sdk_task_filter_test,
        ],
        f'sky jobs cancel -y -n {name}',
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing JobGroup Primary/Auxiliary ----------
@pytest.mark.managed_jobs
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers and auto-stop
@pytest.mark.no_shadeform  # Shadeform does not support host controllers
def test_job_group_primary_auxiliary(generic_cloud: str):
    """Test JobGroup with primary/auxiliary tasks termination behavior.

    Tests that:
    1. Primary task (trainer) completes successfully
    2. Auxiliary task (replay-buffer) is automatically terminated after primary
    3. The termination_delay is respected before auxiliary termination
    4. The job group status is SUCCEEDED when primary succeeds
    """
    name = smoke_tests_utils.get_cluster_name()
    # Use short delay (5s) for faster testing
    delay = '5s'

    # Generate the test YAML using Jinja template
    template_str = pathlib.Path(
        'tests/test_job_groups/smoke_primary_auxiliary.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud=generic_cloud, name=name, delay=delay)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as f:
        f.write(content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'job_group_primary_auxiliary',
            [
                f'sky jobs launch {yaml_path} -y -d',
                # Wait for the job to complete (should succeed based on primary)
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[sky.ManagedJobStatus.SUCCEEDED],
                    timeout=600),
                # Verify primary task succeeded
                f's=$({smoke_tests_utils.GET_JOB_QUEUE} | grep -A 2 {name}); '
                f'echo "$s"; echo "$s" | grep trainer | grep SUCCEEDED',
                # Verify auxiliary task was cancelled (terminated after primary)
                # Check for CANCELLING or CANCELLED as the state may still be
                # transitioning when we check
                f's=$({smoke_tests_utils.GET_JOB_QUEUE} | grep -A 2 {name}); '
                f'echo "$s"; echo "$s" | grep replay-buffer | grep -E "CANCELLING|CANCELLED"',
                # Verify logs show the termination delay message
                f'sky jobs logs --controller -n {name} --no-follow | '
                f'grep -E "Waiting.*before terminating|Terminating auxiliary"',
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.managed_jobs
@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controllers and auto-stop
@pytest.mark.no_shadeform  # Shadeform does not support host controllers
def test_job_group_primary_failure_immediate_termination(generic_cloud: str):
    """Test that auxiliary tasks are terminated immediately when primary fails.

    Tests that:
    1. Primary task fails
    2. Auxiliary task is terminated immediately (no delay, despite config)
    3. The job group status is FAILED
    """
    name = smoke_tests_utils.get_cluster_name()

    # Generate the test YAML using Jinja template
    template_str = pathlib.Path(
        'tests/test_job_groups/smoke_primary_failure.yaml').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud=generic_cloud, name=name)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as f:
        f.write(content)
        f.flush()
        yaml_path = f.name

        test = smoke_tests_utils.Test(
            'job_group_primary_failure',
            [
                f'sky jobs launch {yaml_path} -y -d',
                # Wait for the job to complete (should fail based on primary)
                smoke_tests_utils.
                get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                    job_name=name,
                    job_status=[sky.ManagedJobStatus.FAILED],
                    timeout=600),
                # Verify primary task failed
                f's=$({smoke_tests_utils.GET_JOB_QUEUE} | grep -A 2 {name}); '
                f'echo "$s"; echo "$s" | grep failing-trainer | grep FAILED',
                # Verify auxiliary task was cancelled (terminated immediately)
                # Check for CANCELLING or CANCELLED as the state may still be
                # transitioning when we check
                f's=$({smoke_tests_utils.GET_JOB_QUEUE} | grep -A 2 {name}); '
                f'echo "$s"; echo "$s" | grep replay-buffer | grep -E "CANCELLING|CANCELLED"',
            ],
            f'sky jobs cancel -y -n {name}',
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
            timeout=20 * 60,
        )
        smoke_tests_utils.run_one_test(test)
