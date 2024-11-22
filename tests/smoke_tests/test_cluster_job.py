# Smoke tests for SkyPilot for sky launched cluster and cluster job
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_cluster_job.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_cluster_job.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_cluster_job.py::test_job_queue
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_cluster_job.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_cluster_job.py --generic-cloud aws

import pathlib
import tempfile
import textwrap

import jinja2
import pytest
from smoke_tests.util import BUMP_UP_SECONDS
from smoke_tests.util import get_aws_region_for_quota_failover
from smoke_tests.util import get_cluster_name
from smoke_tests.util import get_gcp_region_for_quota_failover
from smoke_tests.util import get_timeout
from smoke_tests.util import LAMBDA_TYPE
from smoke_tests.util import run_one_test
from smoke_tests.util import SCP_GPU_V100
from smoke_tests.util import SCP_TYPE
from smoke_tests.util import Test
from smoke_tests.util import WAIT_UNTIL_CLUSTER_STATUS_CONTAINS
from smoke_tests.util import WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID

import sky
from sky import AWS
from sky import Azure
from sky import GCP
from sky.skylet import constants
from sky.skylet.job_lib import JobStatus
from sky.status_lib import ClusterStatus
from sky.utils import common_utils
from sky.utils import resources_utils


# ---------- Job Queue. ----------
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue instead
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_job_queue instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_oci  # OCI does not have T4 gpus
def test_job_queue(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'job_queue',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 -d examples/job_queue/job.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Job Queue with Docker. ----------
@pytest.mark.no_fluidstack  # FluidStack does not support docker for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_paperspace  # Paperspace doesn't have T4 GPUs
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
        # Axolotl image is a good example custom image that has its conda path
        # set in PATH with dockerfile and uses python>=3.12. It could test:
        #  1. we handle the env var set in dockerfile correctly
        #  2. python>=3.12 works with SkyPilot runtime.
        'docker:winglian/axolotl:main-latest'
    ])
def test_job_queue_with_docker(generic_cloud: str, image_id: str):
    name = get_cluster_name() + image_id[len('docker:'):][:4]
    total_timeout_minutes = 40 if generic_cloud == 'azure' else 15
    time_to_sleep = 300 if generic_cloud == 'azure' else 180
    test = Test(
        'job_queue_with_docker',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id {image_id} examples/job_queue/cluster_docker.yaml',
            f'sky exec {name} -n {name}-1 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-2 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-3 -d --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            # Make sure the GPU is still visible to the container.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep "Tesla T4"',
            f'sky logs {name} 4 --status',
            f'sky stop -y {name}',
            # Make sure the job status preserve after stop and start the
            # cluster. This is also a test for the docker container to be
            # preserved after stop and start.
            f'sky start -y {name}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep FAILED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep CANCELLED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep CANCELLED',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            # Make sure it is still visible after an stop & start cycle.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep "Tesla T4"',
            f'sky logs {name} 7 --status'
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_job_queue():
    name = get_cluster_name()
    test = Test(
        'lambda_job_queue',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --gpus A10:0.5 -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue():
    name = get_cluster_name()
    test = Test(
        'ibm_job_queue',
        [
            f'sky launch -y -c {name} --cloud ibm --gpus v100',
            f'sky exec {name} -n {name}-1 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-2 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-3 --cloud ibm -d examples/job_queue/job_ibm.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_job_queue():
    name = get_cluster_name()
    num_of_gpu_launch = 1
    num_of_gpu_exec = 0.5
    test = Test(
        'SCP_job_queue',
        [
            f'sky launch -y -c {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 {SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky queue {name} | grep {name}-2 | grep RUNNING',
            f'sky queue {name} | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue_multinode instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus.
@pytest.mark.no_kubernetes  # Kubernetes not support num_nodes > 1 yet
def test_job_queue_multinode(generic_cloud: str):
    name = get_cluster_name()
    total_timeout_minutes = 30 if generic_cloud == 'azure' else 15
    test = Test(
        'job_queue_multinode',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/job_queue/cluster_multinode.yaml',
            f'sky exec {name} -n {name}-1 -d examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -n {name}-2 -d examples/job_queue/job_multinode.yaml',
            f'sky launch -c {name} -n {name}-3 --detach-setup -d examples/job_queue/job_multinode.yaml',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            'sleep 90',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep SETTING_UP',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 --detach-setup -d examples/job_queue/job_multinode.yaml',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            'sleep 5',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus T4:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus T4:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
def test_large_job_queue(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --cloud {generic_cloud}',
            f'for i in `seq 1 75`; do sky exec {name} -n {name}-$i -d "echo $i; sleep 100000000"; done',
            f'sky cancel -y {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 90',

            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # The first 16 jobs are canceled, so there should be 75 - 32 = 43 jobs PENDING.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep PENDING | wc -l | grep 43',
            # Make sure the jobs are scheduled in FIFO order
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(1, 17)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in range(17, 33)
            ],
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep PENDING'
                for i in range(33, 75)
            ],
            f'sky cancel -y {name} 33 35 37 39 17 18 19',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep CANCELLED'
                for i in range(33, 40, 2)
            ],
            'sleep 10',
            *[
                f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-{i} | grep RUNNING'
                for i in [34, 36, 38]
            ],
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
def test_fast_large_job_queue(generic_cloud: str):
    # This is to test the jobs can be scheduled quickly when there are many jobs in the queue.
    name = get_cluster_name()
    test = Test(
        'fast_large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --cloud {generic_cloud}',
            f'for i in `seq 1 32`; do sky exec {name} -n {name}-$i -d "echo $i"; done',
            'sleep 60',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep SUCCEEDED | wc -l | grep 32',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue_multinode():
    name = get_cluster_name()
    task_file = 'examples/job_queue/job_multinode_ibm.yaml'
    test = Test(
        'ibm_job_queue_multinode',
        [
            f'sky launch -y -c {name} --cloud ibm --gpus v100 --num-nodes 2',
            f'sky exec {name} -n {name}-1 -d {task_file}',
            f'sky exec {name} -n {name}-2 -d {task_file}',
            f'sky launch -y -c {name} -n {name}-3 --detach-setup -d {task_file}',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep SETTING_UP)',
            'sleep 90',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 --detach-setup -d {task_file}',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus v100:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus v100:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins
    )
    run_one_test(test)


# ---------- Docker with preinstalled package. ----------
@pytest.mark.no_fluidstack  # Doesn't support Fluidstack for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
# TODO(zhwu): we should fix this for kubernetes
def test_docker_preinstalled_package(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'docker_with_preinstalled_package',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} --image-id docker:nginx',
            f'sky exec {name} "nginx -V"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus
def test_multi_echo(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'multi_echo',
        [
            f'python examples/multi_echo.py {name} {generic_cloud}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 10',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 30',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            'sleep 30',
            # Make sure that our job scheduler is fast enough to have at least
            # 10 RUNNING jobs in parallel.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "RUNNING" | wc -l | awk \'{{if ($1 < 10) exit 1}}\'',
            'sleep 30',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
            f'until sky logs {name} 32 --status; do echo "Waiting for job 32 to finish..."; sleep 1; done',
        ] +
        # Ensure jobs succeeded.
        [
            WAIT_UNTIL_JOB_STATUS_CONTAINS_MATCHING_JOB_ID.format(
                cluster_name=name,
                job_id=i + 1,
                job_status=JobStatus.SUCCEEDED.value,
                timeout=120) for i in range(32)
        ] +
        # Ensure monitor/autoscaler didn't crash on the 'assert not
        # unfulfilled' error.  If process not found, grep->ssh returns 1.
        [f'ssh {name} \'ps aux | grep "[/]"monitor.py\''],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# ---------- Task: 1 node training. ----------
@pytest.mark.no_fluidstack  # Fluidstack does not have T4 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not have V100 (16GB) GPUs. Run test_scp_huggingface instead.
def test_huggingface(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_huggingface(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'lambda_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_huggingface(generic_cloud: str):
    name = get_cluster_name()
    num_of_gpu_launch = 1
    test = Test(
        'SCP_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {SCP_TYPE} {SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Inferentia. ----------
@pytest.mark.aws
def test_inferentia():
    name = get_cluster_name()
    test = Test(
        'test_inferentia',
        [
            f'sky launch -y -c {name} -t inf2.xlarge -- echo hi',
            f'sky exec {name} --gpus Inferentia:1 echo hi',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- TPU. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu():
    name = get_cluster_name()
    test = Test(
        'tpu_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpu_app.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} examples/tpu/tpu_app.yaml | grep "TPU .* already exists"',  # Ensure sky launch won't create another TPU.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take >20 mins
    )
    run_one_test(test)


# ---------- TPU VM. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm():
    name = get_cluster_name()
    test = Test(
        'tpu_vm_app',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep STOPPED',  # Ensure the cluster is STOPPED.
            # Use retry: guard against transient errors observed for
            # just-stopped TPU VMs (#962).
            f'sky start --retry-until-up -y {name}',
            f'sky exec {name} examples/tpu/tpuvm_mnist.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    run_one_test(test)


# ---------- TPU VM Pod. ----------
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm_pod():
    name = get_cluster_name()
    test = Test(
        'tpu_pod',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --gpus tpu-v2-32 --use-spot --zone europe-west4-a',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    run_one_test(test)


# ---------- TPU Pod Slice on GKE. ----------
@pytest.mark.kubernetes
def test_tpu_pod_slice_gke():
    name = get_cluster_name()
    test = Test(
        'tpu_pod_slice_gke',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --cloud kubernetes --gpus tpu-v5-lite-podslice',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "conda activate flax; python -c \'import jax; print(jax.devices()[0].platform);\' | grep tpu || exit 1;"',  # Ensure TPU is reachable.
            f'sky logs {name} 2 --status'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    run_one_test(test)


# ---------- Simple apps. ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_multi_hostname(generic_cloud: str):
    name = get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = Test(
        'multi_hostname',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} examples/multi_hostname.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 1 | grep "My hostname:" | wc -l | grep 2',  # Ensure there are 2 hosts.
            f'sky exec {name} examples/multi_hostname.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=get_timeout(generic_cloud, total_timeout_minutes * 60),
    )
    run_one_test(test)


@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_multi_node_failure(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'multi_node_failure',
        [
            # TODO(zhwu): we use multi-thread to run the commands in setup
            # commands in parallel, which makes it impossible to fail fast
            # when one of the nodes fails. We should fix this in the future.
            # The --detach-setup version can fail fast, as the setup is
            # submitted to the remote machine, which does not use multi-thread.
            # Refer to the comment in `subprocess_utils.run_in_parallel`.
            # f'sky launch -y -c {name} --cloud {generic_cloud} tests/test_yamls/failed_worker_setup.yaml && exit 1',  # Ensure the job setup failed.
            f'sky launch -y -c {name} --cloud {generic_cloud} --detach-setup tests/test_yamls/failed_worker_setup.yaml',
            f'sky logs {name} 1 --status | grep FAILED_SETUP',  # Ensure the job setup failed.
            f'sky exec {name} tests/test_yamls/failed_worker_run.yaml',
            f'sky logs {name} 2 --status | grep FAILED',  # Ensure the job failed.
            f'sky logs {name} 2 | grep "My hostname:" | wc -l | grep 2',  # Ensure there 2 of the hosts printed their hostname.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on GCP. ----------
@pytest.mark.gcp
def test_gcp_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'gcp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud gcp examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on AWS. ----------
@pytest.mark.aws
def test_aws_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'aws_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud aws examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Azure. ----------
@pytest.mark.azure
def test_azure_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'azure_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud azure examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Kubernetes. ----------
@pytest.mark.kubernetes
def test_kubernetes_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'kubernetes_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud kubernetes examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 100); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 5; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on Paperspace. ----------
@pytest.mark.paperspace
def test_paperspace_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'paperspace_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud paperspace examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Web apps with custom ports on RunPod. ----------
@pytest.mark.runpod
def test_runpod_http_server_with_custom_ports():
    name = get_cluster_name()
    test = Test(
        'runpod_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud runpod examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Labels from task on AWS (instance_tags) ----------
@pytest.mark.aws
def test_task_labels_aws():
    name = get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='aws', region='us-east-1')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_aws',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with aws cli that the tags are set.
                'aws ec2 describe-instances '
                '--query "Reservations[*].Instances[*].InstanceId" '
                '--filters "Name=instance-state-name,Values=running" '
                f'--filters "Name=tag:skypilot-cluster-name,Values={name}*" '
                '--filters "Name=tag:inlinelabel1,Values=inlinevalue1" '
                '--filters "Name=tag:inlinelabel2,Values=inlinevalue2" '
                '--region us-east-1 --output text',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Labels from task on GCP (labels) ----------
@pytest.mark.gcp
def test_task_labels_gcp():
    name = get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='gcp')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_gcp',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with gcloud cli that the tags are set
                f'gcloud compute instances list --filter="name~\'^{name}\' AND '
                'labels.inlinelabel1=\'inlinevalue1\' AND '
                'labels.inlinelabel2=\'inlinevalue2\'" '
                '--format="value(name)" | grep .',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Labels from task on Kubernetes (labels) ----------
@pytest.mark.kubernetes
def test_task_labels_kubernetes():
    name = get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='kubernetes')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = Test(
            'task_labels_kubernetes',
            [
                f'sky launch -y -c {name} {file_path}',
                # Verify with kubectl that the labels are set.
                'kubectl get pods '
                '--selector inlinelabel1=inlinevalue1 '
                '--selector inlinelabel2=inlinevalue2 '
                '-o jsonpath=\'{.items[*].metadata.name}\' | '
                f'grep \'^{name}\''
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Pod Annotations on Kubernetes ----------
@pytest.mark.kubernetes
def test_add_pod_annotations_for_autodown_with_launch():
    name = get_cluster_name()
    test = Test(
        'add_pod_annotations_for_autodown_with_launch',
        [
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            # Autodown is set.
            f'sky launch -y -c {name} -i 10 --down --num-nodes 2 --cpus=1 --cloud kubernetes',
            # Get names of the pods containing cluster name.
            f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p)',
            f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p)',
            # Describe the first pod and check for annotations.
            'kubectl describe pod $pod_1 | grep -q skypilot.co/autodown',
            'kubectl describe pod $pod_1 | grep -q skypilot.co/idle_minutes_to_autostop',
            # Describe the second pod and check for annotations.
            'kubectl describe pod $pod_2 | grep -q skypilot.co/autodown',
            'kubectl describe pod $pod_2 | grep -q skypilot.co/idle_minutes_to_autostop'
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.kubernetes
def test_add_and_remove_pod_annotations_with_autostop():
    name = get_cluster_name()
    test = Test(
        'add_and_remove_pod_annotations_with_autostop',
        [
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            f'sky launch -y -c {name} --num-nodes 2 --cpus=1 --cloud kubernetes',
            # Set autodown on the cluster with 'autostop' command.
            f'sky autostop -y {name} -i 20 --down',
            # Get names of the pods containing cluster name.
            f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p)',
            f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p)',
            # Describe the first pod and check for annotations.
            'kubectl describe pod $pod_1 | grep -q skypilot.co/autodown',
            'kubectl describe pod $pod_1 | grep -q skypilot.co/idle_minutes_to_autostop',
            # Describe the second pod and check for annotations.
            'kubectl describe pod $pod_2 | grep -q skypilot.co/autodown',
            'kubectl describe pod $pod_2 | grep -q skypilot.co/idle_minutes_to_autostop',
            # Cancel the set autodown to remove the annotations from the pods.
            f'sky autostop -y {name} --cancel',
            # Describe the first pod and check if annotations are removed.
            '! kubectl describe pod $pod_1 | grep -q skypilot.co/autodown',
            '! kubectl describe pod $pod_1 | grep -q skypilot.co/idle_minutes_to_autostop',
            # Describe the second pod and check if annotations are removed.
            '! kubectl describe pod $pod_2 | grep -q skypilot.co/autodown',
            '! kubectl describe pod $pod_2 | grep -q skypilot.co/idle_minutes_to_autostop',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Container logs from task on Kubernetes ----------
@pytest.mark.kubernetes
def test_container_logs_multinode_kubernetes():
    name = get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    head_logs = ('kubectl get pods '
                 f' | grep {name} |  grep head | '
                 " awk '{print $1}' | xargs -I {} kubectl logs {}")
    worker_logs = ('kubectl get pods '
                   f' | grep {name} |  grep worker |'
                   " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = Test(
            'container_logs_multinode_kubernetes',
            [
                f'sky launch -y -c {name} {task_yaml} --num-nodes 2',
                f'{head_logs} | wc -l | grep 9',
                f'{worker_logs} | wc -l | grep 9',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_jobs_kubernetes():
    name = get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    pod_logs = ('kubectl get pods '
                f' | grep {name} |  grep head |'
                " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = Test(
            'test_container_logs_two_jobs_kubernetes',
            [
                f'sky launch -y -c {name} {task_yaml}',
                f'{pod_logs} | wc -l | grep 9',
                f'sky launch -y -c {name} {task_yaml}',
                f'{pod_logs} | wc -l | grep 18',
                f'{pod_logs} | grep 1 | wc -l | grep 2',
                f'{pod_logs} | grep 2 | wc -l | grep 2',
                f'{pod_logs} | grep 3 | wc -l | grep 2',
                f'{pod_logs} | grep 4 | wc -l | grep 2',
                f'{pod_logs} | grep 5 | wc -l | grep 2',
                f'{pod_logs} | grep 6 | wc -l | grep 2',
                f'{pod_logs} | grep 7 | wc -l | grep 2',
                f'{pod_logs} | grep 8 | wc -l | grep 2',
                f'{pod_logs} | grep 9 | wc -l | grep 2',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_simultaneous_jobs_kubernetes():
    name = get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml '
    pod_logs = ('kubectl get pods '
                f' | grep {name} |  grep head |'
                " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = Test(
            'test_container_logs_two_simultaneous_jobs_kubernetes',
            [
                f'sky launch -y -c {name}',
                f'sky exec -c {name} -d {task_yaml}',
                f'sky exec -c {name} -d {task_yaml}',
                'sleep 30',
                f'{pod_logs} | wc -l | grep 18',
                f'{pod_logs} | grep 1 | wc -l | grep 2',
                f'{pod_logs} | grep 2 | wc -l | grep 2',
                f'{pod_logs} | grep 3 | wc -l | grep 2',
                f'{pod_logs} | grep 4 | wc -l | grep 2',
                f'{pod_logs} | grep 5 | wc -l | grep 2',
                f'{pod_logs} | grep 6 | wc -l | grep 2',
                f'{pod_logs} | grep 7 | wc -l | grep 2',
                f'{pod_logs} | grep 8 | wc -l | grep 2',
                f'{pod_logs} | grep 9 | wc -l | grep 2',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.skip(
    reason=
    'The resnet_distributed_tf_app is flaky, due to it failing to detect GPUs.')
def test_distributed_tf(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            f'python examples/resnet_distributed_tf_app.py {name} {generic_cloud}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
@pytest.mark.gcp
def test_gcp_start_stop():
    name = get_cluster_name()
    test = Test(
        'gcp-start-stop',
        [
            f'sky launch -y -c {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=40),
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=
                f'({ClusterStatus.STOPPED.value}|{ClusterStatus.INIT.value})',
                timeout=200),
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
@pytest.mark.azure
def test_azure_start_stop():
    name = get_cluster_name()
    test = Test(
        'azure-start-stop',
        [
            f'sky launch -y -c {name} examples/azure_start_stop.yaml',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=
                f'({ClusterStatus.STOPPED.value}|{ClusterStatus.INIT.value})',
                timeout=280) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    run_one_test(test)


# ---------- Testing Autostopping ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
def test_autostop(generic_cloud: str):
    name = get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    # Launching and starting Azure clusters can take a long time too. e.g., restart
    # a stopped Azure cluster can take 7m. So we set the total timeout to 70m.
    total_timeout_minutes = 70 if generic_cloud == 'azure' else 20
    test = Test(
        'autostop',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} -i 1',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the cluster is not stopped early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',

            # Ensure the cluster is STOPPED.
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=autostop_timeout),

            # Ensure the cluster is UP and the autostop setting is reset ('-').
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',

            # Ensure the job succeeded.
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',

            # Test restarting the idleness timer via reset:
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 40',  # Almost reached the threshold.
            f'sky autostop -y {name} -i 1',  # Should restart the timer.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s" | grep {name} | grep UP',
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=autostop_timeout),

            # Test restarting the idleness timer via exec:
            f'sky start -y {name}',
            f'sky status | grep {name} | grep -E "UP\s+-"',
            f'sky autostop -y {name} -i 1',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky exec {name} echo hi',  # Should restart the timer.
            'sleep 45',
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=autostop_timeout + BUMP_UP_SECONDS),
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


# ---------- Testing Autodowning ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_autodown instead.
def test_autodown(generic_cloud: str):
    name = get_cluster_name()
    # Azure takes ~ 13m30s (810s) to autodown a VM, so here we use 900 to ensure
    # the VM is terminated.
    autodown_timeout = 900 if generic_cloud == 'azure' else 240
    total_timeout_minutes = 90 if generic_cloud == 'azure' else 20
    test = Test(
        'autodown',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            f'sleep {autodown_timeout}',
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud {generic_cloud} --num-nodes 2 --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            f'sleep {autodown_timeout}',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --cloud {generic_cloud} --num-nodes 2 --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            f'sleep {autodown_timeout}',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    run_one_test(test)


@pytest.mark.scp
def test_scp_autodown():
    name = get_cluster_name()
    test = Test(
        'SCP_autodown',
        [
            f'sky launch -y -d -c {name} {SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} {SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 200',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 200',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    run_one_test(test)


def _get_cancel_task_with_cloud(name, cloud, timeout=15 * 60):
    test = Test(
        f'{cloud}-cancel-task',
        [
            f'sky launch -c {name} examples/resnet_app.yaml --cloud {cloud} -y -d',
            # Wait the GPU process to start.
            'sleep 60',
            f'sky exec {name} "nvidia-smi | grep python"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            # check if the python job is gone.
            f'sky exec {name} "! nvidia-smi | grep python"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=timeout,
    )
    return test


# ---------- Testing `sky cancel` ----------
@pytest.mark.aws
def test_cancel_aws():
    name = get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'aws')
    run_one_test(test)


@pytest.mark.gcp
def test_cancel_gcp():
    name = get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'gcp')
    run_one_test(test)


@pytest.mark.azure
def test_cancel_azure():
    name = get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'azure', timeout=30 * 60)
    run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support V100 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_paperspace  # Paperspace has `gnome-shell` on nvidia-smi
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
def test_cancel_pytorch(generic_cloud: str):
    name = get_cluster_name()
    test = Test(
        'cancel-pytorch',
        [
            f'sky launch -c {name} --cloud {generic_cloud} examples/resnet_distributed_torch.yaml -y -d',
            # Wait the GPU process to start.
            'sleep 90',
            f'sky exec {name} --num-nodes 2 "(nvidia-smi | grep python) || '
            # When run inside container/k8s, nvidia-smi cannot show process ids.
            # See https://github.com/NVIDIA/nvidia-docker/issues/179
            # To work around, we check if GPU utilization is greater than 0.
            f'[ \$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -gt 0 ]"',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            f'sky exec {name} --num-nodes 2 "(nvidia-smi | grep \'No running process\') || '
            # Ensure Xorg is the only process running.
            '[ \$(nvidia-smi | grep -A 10 Processes | grep -A 10 === | grep -v Xorg) -eq 2 ]"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    run_one_test(test)


# can't use `_get_cancel_task_with_cloud()`, as command `nvidia-smi`
# requires a CUDA public image, which IBM doesn't offer
@pytest.mark.ibm
def test_cancel_ibm():
    name = get_cluster_name()
    test = Test(
        'ibm-cancel-task',
        [
            f'sky launch -y -c {name} --cloud ibm examples/minimal.yaml',
            f'sky exec {name} -n {name}-1 -d  "while true; do echo \'Hello SkyPilot\'; sleep 2; done"',
            'sleep 20',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky cancel -y {name} 2',
            f'sleep 5',
            f'sky queue {name} | grep {name}-1 | grep CANCELLED',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing use-spot option ----------
@pytest.mark.no_fluidstack  # FluidStack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
def test_use_spot(generic_cloud: str):
    """Test use-spot and sky exec."""
    name = get_cluster_name()
    test = Test(
        'use-spot',
        [
            f'sky launch -c {name} --cloud {generic_cloud} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status',
            f'sky exec {name} echo hi',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_stop_gcp_spot():
    """Test GCP spot can be stopped, autostopped, restarted."""
    name = get_cluster_name()
    test = Test(
        'stop_gcp_spot',
        [
            f'sky launch -c {name} --cloud gcp --use-spot --cpus 2+ -y -- touch myfile',
            # stop should go through:
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 2 --status',
            f'sky autostop {name} -i0 -y',
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=90),
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 3 --status',
            # -i option at launch should go through:
            f'sky launch -c {name} -i0 -y',
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=ClusterStatus.STOPPED.value,
                timeout=120),
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


# ---------- Testing env ----------
def test_inline_env(generic_cloud: str):
    """Test env"""
    name = get_cluster_name()
    test = Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            'sleep 20',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Testing env file ----------
def test_inline_env_file(generic_cloud: str):
    """Test env"""
    name = get_cluster_name()
    test = Test(
        'test-inline-env-file',
        [
            f'sky launch -c {name} -y --cloud {generic_cloud} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env-file examples/sample_dotenv "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        get_timeout(generic_cloud),
    )
    run_one_test(test)


# ---------- Testing custom image ----------
@pytest.mark.aws
def test_aws_custom_image():
    """Test AWS custom image"""
    name = get_cluster_name()
    test = Test(
        'test-aws-custom-image',
        [
            f'sky launch -c {name} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --cloud aws --region us-east-2 --image-id ami-062ddd90fb6f8267a',  # Nvidia image
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04',
        'docker:ubuntu:18.04',
        # Test latest image with python 3.11 installed by default.
        'docker:continuumio/miniconda3:24.1.2-0',
        # Test python>=3.12 where SkyPilot should automatically create a separate
        # conda env for runtime with python 3.10.
        'docker:continuumio/miniconda3:latest',
    ])
def test_kubernetes_custom_image(image_id):
    """Test Kubernetes custom image"""
    name = get_cluster_name()
    test = Test(
        'test-kubernetes-custom-image',
        [
            f'sky launch -c {name} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --cloud kubernetes --image-id {image_id} --region None --gpus T4:1',
            f'sky logs {name} 1 --status',
            # Try exec to run again and check if the logs are printed
            f'sky exec {name} tests/test_yamls/test_custom_image.yaml --cloud kubernetes --image-id {image_id} --region None --gpus T4:1 | grep "Hello 100"',
            # Make sure ssh is working with custom username
            f'ssh {name} echo hi | grep hi',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_start_stop_two_nodes():
    name = get_cluster_name()
    test = Test(
        'azure-start-stop-two-nodes',
        [
            f'sky launch --num-nodes=2 -y -c {name} examples/azure_start_stop.yaml',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            WAIT_UNTIL_CLUSTER_STATUS_CONTAINS.format(
                cluster_name=name,
                cluster_status=
                f'({ClusterStatus.INIT.value}|{ClusterStatus.STOPPED.value})',
                timeout=200 + BUMP_UP_SECONDS) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    )
    run_one_test(test)


# ---------- Testing env for disk tier ----------
@pytest.mark.aws
def test_aws_disk_tier():

    def _get_aws_query_command(region, instance_id, field, expected):
        return (f'aws ec2 describe-volumes --region {region} '
                f'--filters Name=attachment.instance-id,Values={instance_id} '
                f'--query Volumes[*].{field} | grep {expected} ; ')

    for disk_tier in list(resources_utils.DiskTier):
        specs = AWS._get_disk_specs(disk_tier)
        name = get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.AWS.max_cluster_name_length())
        region = 'us-east-2'
        test = Test(
            'aws-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --cloud aws --region {region} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'id=`aws ec2 describe-instances --region {region} --filters '
                f'Name=tag:ray-cluster-name,Values={name_on_cloud} --query '
                f'Reservations[].Instances[].InstanceId --output text`; ' +
                _get_aws_query_command(region, '$id', 'VolumeType',
                                       specs['disk_tier']) +
                ('' if specs['disk_tier']
                 == 'standard' else _get_aws_query_command(
                     region, '$id', 'Iops', specs['disk_iops'])) +
                ('' if specs['disk_tier'] != 'gp3' else _get_aws_query_command(
                    region, '$id', 'Throughput', specs['disk_throughput'])),
            ],
            f'sky down -y {name}',
            timeout=10 * 60,  # 10 mins  (it takes around ~6 mins)
        )
        run_one_test(test)


@pytest.mark.gcp
def test_gcp_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        disk_types = [GCP._get_disk_type(disk_tier)]
        name = get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.GCP.max_cluster_name_length())
        region = 'us-west2'
        instance_type_options = ['']
        if disk_tier == resources_utils.DiskTier.BEST:
            # Ultra disk tier requires n2 instance types to have more than 64 CPUs.
            # If using default instance type, it will only enable the high disk tier.
            disk_types = [
                GCP._get_disk_type(resources_utils.DiskTier.HIGH),
                GCP._get_disk_type(resources_utils.DiskTier.ULTRA),
            ]
            instance_type_options = ['', '--instance-type n2-standard-64']
        for disk_type, instance_type_option in zip(disk_types,
                                                   instance_type_options):
            test = Test(
                'gcp-disk-tier-' + disk_tier.value,
                [
                    f'sky launch -y -c {name} --cloud gcp --region {region} '
                    f'--disk-tier {disk_tier.value} {instance_type_option} ',
                    f'name=`gcloud compute instances list --filter='
                    f'"labels.ray-cluster-name:{name_on_cloud}" '
                    '--format="value(name)"`; '
                    f'gcloud compute disks list --filter="name=$name" '
                    f'--format="value(type)" | grep {disk_type} '
                ],
                f'sky down -y {name}',
                timeout=6 * 60,  # 6 mins  (it takes around ~3 mins)
            )
            run_one_test(test)


@pytest.mark.azure
def test_azure_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        if disk_tier == resources_utils.DiskTier.HIGH or disk_tier == resources_utils.DiskTier.ULTRA:
            # Azure does not support high and ultra disk tier.
            continue
        type = Azure._get_disk_type(disk_tier)
        name = get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.Azure.max_cluster_name_length())
        region = 'westus2'
        test = Test(
            'azure-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --cloud azure --region {region} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
                f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
                f'--output tsv | grep {type}'
            ],
            f'sky down -y {name}',
            timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
        )
        run_one_test(test)


@pytest.mark.azure
def test_azure_best_tier_failover():
    type = Azure._get_disk_type(resources_utils.DiskTier.LOW)
    name = get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Azure.max_cluster_name_length())
    region = 'westus2'
    test = Test(
        'azure-best-tier-failover',
        [
            f'sky launch -y -c {name} --cloud azure --region {region} '
            f'--disk-tier best --instance-type Standard_D8_v5 echo "hello sky"',
            f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
            f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
            f'--output tsv | grep {type}',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
    )
    run_one_test(test)


# ------ Testing Zero Quota Failover ------
@pytest.mark.aws
def test_aws_zero_quota_failover():

    name = get_cluster_name()
    region = get_aws_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for EC2 P3 instances were found on all AWS regions. Is this '
            'expected for your account?')
        return

    test = Test(
        'aws-zero-quota-failover',
        [
            f'sky launch -y -c {name} --cloud aws --region {region} --gpus V100:8 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_zero_quota_failover():

    name = get_cluster_name()
    region = get_gcp_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for A100-80GB GPUs were found on all GCP regions. Is this '
            'expected for your account?')
        return

    test = Test(
        'gcp-zero-quota-failover',
        [
            f'sky launch -y -c {name} --cloud gcp --region {region} --gpus A100-80GB:1 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


def test_long_setup_run_script(generic_cloud: str):
    name = get_cluster_name()
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_',
                                     suffix='.yaml') as f:
        f.write(
            textwrap.dedent(""" \
            setup: |
              echo "start long setup"
            """))
        for i in range(1024 * 200):
            f.write(f'  echo {i}\n')
        f.write('  echo "end long setup"\n')
        f.write(
            textwrap.dedent(""" \
            run: |
              echo "run"
        """))
        for i in range(1024 * 200):
            f.write(f'  echo {i}\n')
        f.write('  echo "end run"\n')
        f.flush()

        test = Test(
            'long-setup-run-script',
            [
                f'sky launch -y -c {name} --cloud {generic_cloud} --cpus 2+ {f.name}',
                f'sky exec {name} "echo hello"',
                f'sky exec {name} {f.name}',
                f'sky logs {name} --status 1',
                f'sky logs {name} --status 2',
                f'sky logs {name} --status 3',
            ],
            f'sky down -y {name}',
        )
        run_one_test(test)
