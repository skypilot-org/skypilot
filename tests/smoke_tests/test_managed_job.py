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
import tempfile
import time

import pytest
from smoke_tests.util import _BUMP_UP_SECONDS
from smoke_tests.util import get_cluster_name
from smoke_tests.util import GET_JOB_QUEUE
from smoke_tests.util import JOB_WAIT_NOT_RUNNING
from smoke_tests.util import run_one_test
from smoke_tests.util import STORAGE_SETUP_COMMANDS
from smoke_tests.util import Test
from smoke_tests.util import TestStorageWithCredentials
from smoke_tests.util import (
    WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME)

from sky import jobs
from sky.data import storage as storage_lib
from sky.jobs.state import ManagedJobStatus
from sky.skylet import constants
from sky.utils import common_utils


# ---------- Testing managed job ----------
# TODO(zhwu): make the jobs controller on GCP, to avoid parallel test issues
# when the controller being on Azure, which takes a long time for launching
# step.
@pytest.mark.managed_jobs
def test_managed_jobs(generic_cloud: str):
    """Test the managed jobs yaml."""
    name = get_cluster_name()
    test = Test(
        'managed-jobs',
        [
            f'sky jobs launch -n {name}-1 --cloud {generic_cloud} examples/managed_job.yaml -y -d',
            f'sky jobs launch -n {name}-2 --cloud {generic_cloud} examples/managed_job.yaml -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-1',
                job_status=
                f'({ManagedJobStatus.PENDING.value}|{ManagedJobStatus.INIT.value}|{ManagedJobStatus.RUNNING.value})',
                timeout=60),
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-2',
                job_status=
                f'({ManagedJobStatus.PENDING.value}|{ManagedJobStatus.INIT.value}|{ManagedJobStatus.RUNNING.value})',
                timeout=60),
            f'sky jobs cancel -y -n {name}-1',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-1',
                job_status=f'{ManagedJobStatus.CANCELLED.value}',
                timeout=230),
            # Test the functionality for logging.
            f's=$(sky jobs logs -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "start counting"',
            f's=$(sky jobs logs --controller -n {name}-2 --no-follow); echo "$s"; echo "$s" | grep "Cluster launched:"',
            f'{GET_JOB_QUEUE} | grep {name}-2 | head -n1 | grep "RUNNING\|SUCCEEDED"',
        ],
        # TODO(zhwu): Change to f'sky jobs cancel -y -n {name}-1 -n {name}-2' when
        # canceling multiple job names is supported.
        f'sky jobs cancel -y -n {name}-1; sky jobs cancel -y -n {name}-2',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_job_pipeline(generic_cloud: str):
    """Test a job pipeline."""
    name = get_cluster_name()
    test = Test(
        'spot-pipeline',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline.yaml -y -d',
            'sleep 5',
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "STARTING\|RUNNING"',
            # `grep -A 4 {name}` finds the job with {name} and the 4 lines
            # after it, i.e. the 4 tasks within the job.
            # `sed -n 2p` gets the second line of the 4 lines, i.e. the first
            # task within the job.
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "STARTING\|RUNNING"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "PENDING"',
            f'sky jobs cancel -y -n {name}',
            'sleep 5',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "CANCELLING\|CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "CANCELLING\|CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLING\|CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLING\|CANCELLED"',
            'sleep 200',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        f'sky jobs cancel -y -n {name}',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_failed_setup(generic_cloud: str):
    """Test managed job with failed setup."""
    name = get_cluster_name()
    test = Test(
        'managed_jobs_failed_setup',
        [
            f'sky jobs launch -n {name} --cloud {generic_cloud} -y -d tests/test_yamls/failed_setup.yaml',
            # Make sure the job failed quickly.
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=f'{ManagedJobStatus.FAILED_SETUP.value}',
                timeout=330 + _BUMP_UP_SECONDS),
        ],
        f'sky jobs cancel -y -n {name}',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  #fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_failed_setup(generic_cloud: str):
    """Test managed job with failed setup for a pipeline."""
    name = get_cluster_name()
    test = Test(
        'managed_jobs_pipeline_failed_setup',
        [
            f'sky jobs launch -n {name} -y -d tests/test_yamls/failed_setup_pipeline.yaml',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=f'{ManagedJobStatus.FAILED_SETUP.value}',
                timeout=600),
            # Make sure the job failed quickly.
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "FAILED_SETUP"',
            # Task 0 should be SUCCEEDED.
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 2p | grep "SUCCEEDED"',
            # Task 1 should be FAILED_SETUP.
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 3p | grep "FAILED_SETUP"',
            # Task 2 should be CANCELLED.
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 4p | grep "CANCELLED"',
            # Task 3 should be CANCELLED.
            f'{GET_JOB_QUEUE} | grep -A 4 {name}| sed -n 5p | grep "CANCELLED"',
        ],
        f'sky jobs cancel -y -n {name}',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=30 * 60,
    )
    run_one_test(test)


# ---------- Testing managed job recovery ----------


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_aws(aws_config_region):
    """Test managed job recovery."""
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_recovery_aws',
        [
            f'sky jobs launch --cloud aws --region {region} --use-spot -n {name} "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=600),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_gcp():
    """Test managed job recovery."""
    name = get_cluster_name()
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
    test = Test(
        'managed_jobs_recovery_gcp',
        [
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot --cpus 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=300),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the cluster manually.
            terminate_cmd,
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo "$RUN_ID"; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_aws(aws_config_region):
    """Test managed job recovery for a pipeline."""
    name = get_cluster_name()
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    region = aws_config_region
    if region != 'us-east-2':
        pytest.skip('Only run spot pipeline recovery test in us-east-2')
    test = Test(
        'managed_jobs_pipeline_recovery_aws',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline_aws.yaml  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (
                f'MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
                'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`;'
                f'aws ec2 terminate-instances --region {region} --instance-ids $('
                f'aws ec2 describe-instances --region {region} '
                # TODO(zhwu): fix the name for spot cluster.
                '--filters Name=tag:ray-cluster-name,Values=*-${MANAGED_JOB_ID}'
                f'-{user_hash} '
                f'--query Reservations[].Instances[].InstanceId '
                '--output text)'),
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_pipeline_recovery_gcp():
    """Test managed job recovery for a pipeline."""
    name = get_cluster_name()
    zone = 'us-east4-b'
    user_hash = common_utils.get_user_hash()
    user_hash = user_hash[:common_utils.USER_HASH_LENGTH_IN_CLUSTER_NAME]
    query_cmd = (
        'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:*-${{MANAGED_JOB_ID}}-{user_hash})" '
        f'--zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_pipeline_recovery_gcp',
        [
            f'sky jobs launch -n {name} tests/test_yamls/pipeline_gcp.yaml  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids',
            # Terminate the cluster manually.
            # The `cat ...| rev` is to retrieve the job_id from the
            # SKYPILOT_TASK_ID, which gets the second to last field
            # separated by `-`.
            (f'MANAGED_JOB_ID=`cat /tmp/{name}-run-id | rev | '
             f'cut -d\'_\' -f1 | rev | cut -d\'-\' -f1`; {terminate_cmd}'),
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=200),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID: | grep "$RUN_ID"',
            f'RUN_IDS=$(sky jobs logs -n {name} --no-follow | grep -A 4 SKYPILOT_TASK_IDS | cut -d")" -f2); echo "$RUN_IDS" | tee /tmp/{name}-run-ids-new',
            f'diff /tmp/{name}-run-ids /tmp/{name}-run-ids-new',
            f'cat /tmp/{name}-run-ids | sed -n 2p | grep `cat /tmp/{name}-run-id`',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_default_resources(generic_cloud: str):
    """Test managed job recovery for default resources."""
    name = get_cluster_name()
    test = Test(
        'managed-spot-recovery-default-resources',
        [
            f'sky jobs launch -n {name} --cloud {generic_cloud} --use-spot "sleep 30 && sudo shutdown now && sleep 1000" -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=
                f'({ManagedJobStatus.RUNNING.value}|{ManagedJobStatus.RECOVERING.value})',
                timeout=360),
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_aws(aws_config_region):
    """Test managed job recovery."""
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_recovery_multi_node_aws',
        [
            f'sky jobs launch --cloud aws --region {region} -n {name} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=450),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}* '
             'Name=tag:ray-node-type,Values=worker '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=560),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_recovery_multi_node_gcp():
    """Test managed job recovery."""
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    zone = 'us-west2-a'
    # Use ':' to match as the cluster name will contain the suffix with job id
    query_cmd = (
        f'gcloud compute instances list --filter='
        f'"(labels.ray-cluster-name:{name_on_cloud} AND '
        f'labels.ray-node-type=worker)" --zones={zone} --format="value(name)"')
    terminate_cmd = (f'gcloud compute instances delete --zone={zone}'
                     f' --quiet $({query_cmd})')
    test = Test(
        'managed_jobs_recovery_multi_node_gcp',
        [
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot --num-nodes 2 "echo SKYPILOT_TASK_ID: \$SKYPILOT_TASK_ID; sleep 1800"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=400),
            f'RUN_ID=$(sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2); echo "$RUN_ID" | tee /tmp/{name}-run-id',
            # Terminate the worker manually.
            terminate_cmd,
            JOB_WAIT_NOT_RUNNING.format(job_name=name),
            f'{GET_JOB_QUEUE} | grep {name} | head -n1 | grep "RECOVERING"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=560),
            f'RUN_ID=$(cat /tmp/{name}-run-id); echo $RUN_ID; sky jobs logs -n {name} --no-follow | grep SKYPILOT_TASK_ID | cut -d: -f2 | grep "$RUN_ID"',
        ],
        f'sky jobs cancel -y -n {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.aws
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_aws(aws_config_region):
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_2_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-2', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    name_3_on_cloud = common_utils.make_cluster_name_on_cloud(
        f'{name}-3', jobs.JOBS_CLUSTER_NAME_PREFIX_LENGTH, add_user_hash=False)
    region = aws_config_region
    test = Test(
        'managed_jobs_cancellation_aws',
        [
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --cloud aws --region {region} -n {name} --use-spot "sleep 1000"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=
                f'({ManagedJobStatus.STARTING.value}|{ManagedJobStatus.RUNNING.value})',
                timeout=60 + _BUMP_UP_SECONDS),
            f'sky jobs cancel -y -n {name}',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --cloud aws --region {region} -n {name}-2 --use-spot tests/test_yamls/test_long_setup.yaml  -y -d',
            # The job is set up in the cluster, will shown as RUNNING.
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-2',
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=300 + _BUMP_UP_SECONDS),
            f'sky jobs cancel -y -n {name}-2',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-2',
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_2_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || [[ "$s" = "terminated" ]] || [[ "$s" = "shutting-down" ]]'
            ),
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --cloud aws --region {region} -n {name}-3 --use-spot "sleep 1000"  -y -d',
            # The job is running in the cluster, will shown as RUNNING.
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-3',
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=300 + _BUMP_UP_SECONDS),
            # Terminate the cluster manually.
            (f'aws ec2 terminate-instances --region {region} --instance-ids $('
             f'aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_3_on_cloud}-* '
             f'--query Reservations[].Instances[].InstanceId '
             '--output text)'),
            JOB_WAIT_NOT_RUNNING.format(job_name=f'{name}-3'),
            f'{GET_JOB_QUEUE} | grep {name}-3 | head -n1 | grep "RECOVERING"',
            f'sky jobs cancel -y -n {name}-3',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-3',
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            # The cluster should be terminated (shutting-down) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$(aws ec2 describe-instances --region {region} '
             f'--filters Name=tag:ray-cluster-name,Values={name_3_on_cloud}-* '
             f'--query Reservations[].Instances[].State[].Name '
             '--output text) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "pending|running|stopped|stopping"'
            ),
        ],
        timeout=25 * 60)
    run_one_test(test)


@pytest.mark.gcp
@pytest.mark.managed_jobs
def test_managed_jobs_cancellation_gcp():
    name = get_cluster_name()
    name_3 = f'{name}-3'
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
    test = Test(
        'managed_jobs_cancellation_gcp',
        [
            # Test cancellation during spot cluster being launched.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name} --use-spot "sleep 1000"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.STARTING.value,
                timeout=60 + _BUMP_UP_SECONDS),
            f'sky jobs cancel -y -n {name}',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            # Test cancelling the spot cluster during spot job being setup.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name}-2 --use-spot tests/test_yamls/test_long_setup.yaml  -y -d',
            # The job is set up in the cluster, will shown as RUNNING.
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-2',
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=300 + _BUMP_UP_SECONDS),
            f'sky jobs cancel -y -n {name}-2',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-2',
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            # Test cancellation during spot job is recovering.
            f'sky jobs launch --cloud gcp --zone {zone} -n {name}-3 --use-spot "sleep 1000"  -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-3',
                job_status=ManagedJobStatus.RUNNING.value,
                timeout=300 + _BUMP_UP_SECONDS),
            # Terminate the cluster manually.
            terminate_cmd,
            JOB_WAIT_NOT_RUNNING.format(job_name=f'{name}-3'),
            f'{GET_JOB_QUEUE} | grep {name}-3 | head -n1 | grep "RECOVERING"',
            f'sky jobs cancel -y -n {name}-3',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=f'{name}-3',
                job_status=ManagedJobStatus.CANCELLED.value,
                timeout=120 + _BUMP_UP_SECONDS),
            # The cluster should be terminated (STOPPING) after cancellation. We don't use the `=` operator here because
            # there can be multiple VM with the same name due to the recovery.
            (f's=$({query_state_cmd}) && echo "$s" && echo; [[ -z "$s" ]] || echo "$s" | grep -v -E "PROVISIONING|STAGING|RUNNING|REPAIRING|TERMINATED|SUSPENDING|SUSPENDED|SUSPENDED"'
            ),
        ],
        timeout=25 * 60)
    run_one_test(test)


# ---------- Testing storage for managed job ----------
@pytest.mark.no_fluidstack  # Fluidstack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.managed_jobs
def test_managed_jobs_storage(generic_cloud: str):
    """Test storage with managed job"""
    name = get_cluster_name()
    yaml_str = pathlib.Path(
        'examples/managed_job_with_storage.yaml').read_text()
    timestamp = int(time.time())
    storage_name = f'sky-test-{timestamp}'
    output_storage_name = f'sky-test-output-{timestamp}'

    # Also perform region testing for bucket creation to validate if buckets are
    # created in the correct region and correctly mounted in managed jobs.
    # However, we inject this testing only for AWS and GCP since they are the
    # supported object storage providers in SkyPilot.
    region_flag = ''
    region_validation_cmd = 'true'
    use_spot = ' --use-spot'
    if generic_cloud == 'aws':
        region = 'eu-central-1'
        region_flag = f' --region {region}'
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.S3, bucket_name=storage_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        s3_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        output_check_cmd = f'{s3_check_file_count} | grep 1'
    elif generic_cloud == 'gcp':
        region = 'us-west2'
        region_flag = f' --region {region}'
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.GCS, bucket_name=storage_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        gcs_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        output_check_cmd = f'{gcs_check_file_count} | grep 1'
    elif generic_cloud == 'azure':
        region = 'westus2'
        region_flag = f' --region {region}'
        storage_account_name = (
            storage_lib.AzureBlobStore.get_default_storage_account_name(region))
        region_cmd = TestStorageWithCredentials.cli_region_cmd(
            storage_lib.StoreType.AZURE,
            storage_account_name=storage_account_name)
        region_validation_cmd = f'{region_cmd} | grep {region}'
        az_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.AZURE,
            output_storage_name,
            'output.txt',
            storage_account_name=storage_account_name)
        output_check_cmd = f'{az_check_file_count} | grep 1'
    elif generic_cloud == 'kubernetes':
        # With Kubernetes, we don't know which object storage provider is used.
        # Check both S3 and GCS if bucket exists in either.
        s3_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.S3, output_storage_name, 'output.txt')
        s3_output_check_cmd = f'{s3_check_file_count} | grep 1'
        gcs_check_file_count = TestStorageWithCredentials.cli_count_name_in_bucket(
            storage_lib.StoreType.GCS, output_storage_name, 'output.txt')
        gcs_output_check_cmd = f'{gcs_check_file_count} | grep 1'
        output_check_cmd = f'{s3_output_check_cmd} || {gcs_output_check_cmd}'
        use_spot = ' --no-use-spot'

    yaml_str = yaml_str.replace('sky-workdir-zhwu', storage_name)
    yaml_str = yaml_str.replace('sky-output-bucket', output_storage_name)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(yaml_str)
        f.flush()
        file_path = f.name
        test = Test(
            'managed_jobs_storage',
            [
                *STORAGE_SETUP_COMMANDS,
                f'sky jobs launch -n {name}{use_spot} --cloud {generic_cloud}{region_flag} {file_path} -y',
                region_validation_cmd,  # Check if the bucket is created in the correct region
                WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                    job_name=name,
                    job_status=ManagedJobStatus.SUCCEEDED.value,
                    timeout=60 + _BUMP_UP_SECONDS),
                f'[ $(aws s3api list-buckets --query "Buckets[?contains(Name, \'{storage_name}\')].Name" --output text | wc -l) -eq 0 ]',
                # Check if file was written to the mounted output bucket
                output_check_cmd
            ],
            (f'sky jobs cancel -y -n {name}',
             f'; sky storage delete {output_storage_name} || true'),
            # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
            timeout=20 * 60,
        )
        run_one_test(test)


# ---------- Testing spot TPU ----------
@pytest.mark.gcp
@pytest.mark.managed_jobs
@pytest.mark.tpu
def test_managed_jobs_tpu():
    """Test managed job on TPU."""
    name = get_cluster_name()
    test = Test(
        'test-spot-tpu',
        [
            f'sky jobs launch -n {name} --use-spot examples/tpu/tpuvm_mnist.yaml -y -d',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.STARTING.value,
                timeout=60 + _BUMP_UP_SECONDS),
            # TPU takes a while to launch
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=
                f'({ManagedJobStatus.RUNNING.value}|{ManagedJobStatus.SUCCEEDED.value})',
                timeout=900 + _BUMP_UP_SECONDS),
        ],
        f'sky jobs cancel -y -n {name}',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Testing env for managed jobs ----------
@pytest.mark.managed_jobs
def test_managed_jobs_inline_env(generic_cloud: str):
    """Test managed jobs env"""
    name = get_cluster_name()
    test = Test(
        'test-managed-jobs-inline-env',
        [
            f'sky jobs launch -n {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            WAIT_UNTIL_MANAGED_JOB_STATUS_CONTAINS_MATCHING_JOB_NAME.format(
                job_name=name,
                job_status=ManagedJobStatus.SUCCEEDED.value,
                timeout=20 + _BUMP_UP_SECONDS),
        ],
        f'sky jobs cancel -y -n {name}',
        # Increase timeout since sky jobs queue -r can be blocked by other spot tests.
        timeout=20 * 60,
    )
    run_one_test(test)
