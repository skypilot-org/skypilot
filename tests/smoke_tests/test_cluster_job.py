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

import os
import pathlib
import shlex
import tempfile
import textwrap
from typing import Dict, List

import jinja2
import pytest
from smoke_tests import smoke_tests_utils
from smoke_tests.docker import docker_utils

import sky
from sky import AWS
from sky import Azure
from sky import GCP
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import resources_utils


# ---------- Job Queue. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_shadeform  # Shadeform does not have T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue instead
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_job_queue instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_oci  # OCI does not have T4 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.no_seeweb  # Seeweb does not support T4 GPUs
@pytest.mark.no_slurm  # Slurm does not support allocating fractional GPUs in a job. Run test_job_queue_multi_gpu instead
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_job_queue(generic_cloud: str, accelerator: Dict[str, str]):
    if generic_cloud in ('kubernetes', 'slurm'):
        accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud)
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    if not accelerator:
        pytest.skip(f'No available GPUs for {generic_cloud}')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'job_queue',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 -d --gpus {accelerator}:0.5 examples/job_queue/job.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Job Queue with Docker. ----------
@pytest.mark.no_fluidstack  # FluidStack does not support docker for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_shadeform  # Shadeform does not have T4 GPUs
@pytest.mark.no_paperspace  # Paperspace doesn't have T4 GPUs
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.no_hyperbolic  # Doesn't support Hyperbolic for now
@pytest.mark.no_seeweb  # Seeweb does not support Docker images
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
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
def test_job_queue_with_docker(generic_cloud: str, image_id: str,
                               accelerator: Dict[str, str]):
    accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name() + image_id[len('docker:'):][:4]
    total_timeout_minutes = 40 if generic_cloud == 'azure' else 15
    time_to_sleep = 300 if generic_cloud == 'azure' else 200
    # Nebius support Cuda >= 12.0
    if (image_id == 'docker:nvidia/cuda:11.8.0-devel-ubuntu18.04' and
            generic_cloud == 'nebius'):
        image_id = 'docker:nvidia/cuda:12.1.0-devel-ubuntu18.04'

    test = smoke_tests_utils.Test(
        'job_queue_with_docker',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} --image-id {image_id} examples/job_queue/cluster_docker.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep*2} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f'sky exec {name} -n {name}-3 -d --gpus {accelerator}:0.5 --image-id {image_id} --env TIME_TO_SLEEP={time_to_sleep} examples/job_queue/job_docker.yaml',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            f'sky cancel -y {name} 2',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 3',
            # Make sure the GPU is still visible to the container.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep -i "{accelerator}"',
            f'sky logs {name} 4 --status',
            f'sky stop -y {name}',
            # Make sure the job status preserve after stop and start the
            # cluster. This is also a test for the docker container to be
            # preserved after stop and start.
            f'sky start -y {name}',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep FAILED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep CANCELLED',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep CANCELLED',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            # Make sure it is still visible after an stop & start cycle.
            f'sky exec {name} --image-id {image_id} nvidia-smi | grep -i "{accelerator}"',
            f'sky logs {name} 7 --status'
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'lambda_job_queue',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LAMBDA_TYPE} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 --gpus {smoke_tests_utils.LAMBDA_GPU_TYPE}:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 --gpus {smoke_tests_utils.LAMBDA_GPU_TYPE}:0.5 -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 --gpus {smoke_tests_utils.LAMBDA_GPU_TYPE}:0.5 -d examples/job_queue/job.yaml',
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
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'ibm_job_queue',
        [
            f'sky launch -y -c {name} --infra ibm --gpus v100',
            f'sky exec {name} -n {name}-1 --infra ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-2 --infra ibm -d examples/job_queue/job_ibm.yaml',
            f'sky exec {name} -n {name}-3 --infra ibm -d examples/job_queue/job_ibm.yaml',
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
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_job_queue():
    name = smoke_tests_utils.get_cluster_name()
    num_of_gpu_launch = 1
    num_of_gpu_exec = 0.5
    test = smoke_tests_utils.Test(
        'SCP_job_queue',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/job_queue/cluster.yaml',
            f'sky exec {name} -n {name}-1 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-2 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
            f'sky exec {name} -n {name}-3 {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_exec} -d examples/job_queue/job.yaml',
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
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_shadeform  # Shadeform does not have T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus. run test_ibm_job_queue_multinode instead
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus.
@pytest.mark.no_scp  # SCP does not have T4 gpus.
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus.
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
@pytest.mark.no_slurm  # Slurm does not support allocating fractional GPUs in a job. Run test_job_queue_multi_gpu instead
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_job_queue_multinode(generic_cloud: str, accelerator: Dict[str, str]):
    if generic_cloud in ('kubernetes', 'slurm'):
        accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud)
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    if not accelerator:
        pytest.skip(f'No available GPUs for {generic_cloud}')
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 30 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'job_queue_multinode',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/job_queue/cluster_multinode.yaml',
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f'sky launch -c {name} -n {name}-3 -d --gpus {accelerator}:0.5 examples/job_queue/job_multinode.yaml',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            'sleep 90',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep SETTING_UP',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 -d --gpus {accelerator} examples/job_queue/job_multinode.yaml',
            # Test the job status is correctly set to SETTING_UP, during the setup is running,
            # and the job can be cancelled during the setup.
            'sleep 5',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep SETTING_UP)',
            f'sky cancel -y {name} 4',
            f's=$(sky queue {name}) && echo "$s" && (echo "$s" | grep {name}-4 | grep CANCELLED)',
            f'sky exec {name} --gpus {accelerator}:0.2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:0.2 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky exec {name} --gpus {accelerator}:1 --num-nodes 2 "[[ \$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1"',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.slurm
def test_job_queue_multi_gpu(generic_cloud: str):
    accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud,
                                                       count=4)
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'job_queue_multi_gpu',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} --cpus 16 --gpus {accelerator}:4 examples/job_queue/cluster.yaml',
            # Submit job 1 with 1 GPU
            f'sky exec {name} -n {name}-1 -d --gpus {accelerator}:1 "[[ \\$SKYPILOT_NUM_GPUS_PER_NODE -eq 1 ]] || exit 1; num_devices=\\$(echo \\$CUDA_VISIBLE_DEVICES | tr \',\' \'\\n\' | wc -l); [[ \\$num_devices -eq 1 ]] || exit 1; sleep 30"',
            # Submit job 2 with 2 GPUs
            f'sky exec {name} -n {name}-2 -d --gpus {accelerator}:2 "[[ \\$SKYPILOT_NUM_GPUS_PER_NODE -eq 2 ]] || exit 1; num_devices=\\$(echo \\$CUDA_VISIBLE_DEVICES | tr \',\' \'\\n\' | wc -l); [[ \\$num_devices -eq 2 ]] || exit 1; sleep 30"',
            # Submit job 3 with 4 GPUs - should be blocked until the first two jobs complete
            f'sky exec {name} -n {name}-3 -d --gpus {accelerator}:4 "[[ \\$SKYPILOT_NUM_GPUS_PER_NODE -eq 4 ]] || exit 1; num_devices=\\$(echo \\$CUDA_VISIBLE_DEVICES | tr \',\' \'\\n\' | wc -l); [[ \\$num_devices -eq 4 ]] || exit 1"',
            # First two jobs should be running, and job 3 should be pending.
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-1 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-2 | grep RUNNING',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep {name}-3 | grep PENDING',
            'sleep 60',
            # Verify all jobs succeed.
            f'sky logs {name} 1 --status && sky logs {name} 1',
            f'sky logs {name} 2 --status && sky logs {name} 2',
            f'sky logs {name} 3 --status && sky logs {name} 3',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
@pytest.mark.no_vast  # Vast doesn't guarantee exactly 8 CPUs, only at least.
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic
def test_large_job_queue(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # For Slurm, by default it will bind to physical cores, which means that when using instance types with
    # simultaneous multithreading (SMT), we would only run half as many parallel tasks,
    # as each task will get 1 core = 2 vCPUs.
    # Plus slurm cant do fracttional CPUs, so we need to use more CPUs to ensure that we can run more tasks in parallel.
    # So we need to use more CPUs to ensure that we can run more tasks in parallel.
    # For other clouds, we use the default 8 CPUs.
    cpus = 8 if generic_cloud != 'slurm' else 16 * 2
    test = smoke_tests_utils.Test(
        'large_job_queue',
        [
            f'sky launch -y -c {name} --cpus {cpus} --infra {generic_cloud}',
            f'for i in `seq 1 75`; do sky exec {name} -n {name}-$i -d "echo $i; sleep 100000000"; done',
            f'sky cancel -y {name} 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16',
            'sleep 90',

            # Each job takes 0.5 CPU and the default VM has 8 CPUs, so there should be 8 / 0.5 = 16 jobs running.
            # Slurm is an exception here, where to get 16 parallel tasks running, we need an equal
            # amount of physical cores, not vCPUs. So assuming the instance type in our slurm custer
            # has two HW threads per core, we then need 16 * 2 = 32 physical cores.
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
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # No FluidStack VM has 8 CPUs
@pytest.mark.no_lambda_cloud  # No Lambda Cloud VM has 8 CPUs
@pytest.mark.no_vast  # No Vast Cloud VM has 8 CPUs
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic
@pytest.mark.resource_heavy
def test_fast_large_job_queue(generic_cloud: str):
    # This is to test the jobs can be scheduled quickly when there are many jobs in the queue.
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'fast_large_job_queue',
        [
            f'sky launch -y -c {name} --cpus 8 --infra {generic_cloud}',
            f'for i in `seq 1 32`; do sky exec {name} -n {name}-$i -d "echo $i"; done',
            'sleep 60',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep -v grep | grep SUCCEEDED | wc -l | grep 32',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.ibm
def test_ibm_job_queue_multinode():
    name = smoke_tests_utils.get_cluster_name()
    task_file = 'examples/job_queue/job_multinode_ibm.yaml'
    test = smoke_tests_utils.Test(
        'ibm_job_queue_multinode',
        [
            f'sky launch -y -c {name} --infra ibm --gpus v100 --num-nodes 2',
            f'sky exec {name} -n {name}-1 -d {task_file}',
            f'sky exec {name} -n {name}-2 -d {task_file}',
            f'sky launch -y -c {name} -n {name}-3 -d {task_file}',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-1 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-2 | grep RUNNING)',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep SETTING_UP)',
            'sleep 90',
            f's=$(sky queue {name}) && printf "$s" && (echo "$s" | grep {name}-3 | grep PENDING)',
            f'sky cancel -y {name} 1',
            'sleep 5',
            f'sky queue {name} | grep {name}-3 | grep RUNNING',
            f'sky cancel -y {name} 1 2 3',
            f'sky launch -c {name} -n {name}-4 -d {task_file}',
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
    smoke_tests_utils.run_one_test(test)


# ---------- Docker with preinstalled package. ----------
@pytest.mark.no_fluidstack  # Doesn't support Fluidstack for now
@pytest.mark.no_lambda_cloud  # Doesn't support Lambda Cloud for now
@pytest.mark.no_ibm  # Doesn't support IBM Cloud for now
@pytest.mark.no_scp  # Doesn't support SCP for now
@pytest.mark.no_oci  # Doesn't support OCI for now
@pytest.mark.no_kubernetes  # Doesn't support Kubernetes for now
@pytest.mark.no_hyperbolic  # Doesn't support Hyperbolic for now
@pytest.mark.no_shadeform  # Doesn't support Shadeform for now
@pytest.mark.no_seeweb  # Seeweb does not support Docker images
def test_docker_preinstalled_package(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'docker_with_preinstalled_package',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id docker:nginx',
            f'sky exec {name} "nginx -V"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.slurm
def test_docker_preinstalled_package_slurm_sqsh(generic_cloud: str):
    """Test local .sqsh container images on Slurm (both absolute and relative paths)."""
    name = smoke_tests_utils.get_cluster_name()
    name_rel = f'{name}-rel'

    # Parse "sky check slurm" output: "    ├── cluster: enabled" -> "cluster"
    # sed strips ANSI escape codes from colored output
    get_slurm_cluster = ("sky check slurm 2>&1 | "
                         "sed 's/\\x1b\\[[0-9;]*m//g' | "
                         "grep -E '(├──|└──)' | "
                         "grep 'enabled' | "
                         "head -1 | "
                         "awk -F': ' '{print $1}' | "
                         "awk '{print $NF}'")

    test = smoke_tests_utils.Test(
        'docker_preinstalled_sqsh',
        [
            # Get slurm cluster name and remote home directory
            f'SLURM_CLUSTER=$({get_slurm_cluster}) && '
            f'echo "Using Slurm cluster: $SLURM_CLUSTER" && '
            f'SLURM_HOME=$(ssh -F ~/.slurm/config $SLURM_CLUSTER "echo \\$HOME") && '
            f'echo "Remote home: $SLURM_HOME" && '
            # Import nginx image to create .sqsh file in ~/nginx+latest.sqsh
            f'ssh -F ~/.slurm/config $SLURM_CLUSTER "srun enroot import docker://nginx:latest" && '
            # Test 1: Absolute path - launch with full path to .sqsh
            f'sky launch -y -c {name} --infra slurm/$SLURM_CLUSTER '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--image-id docker:$SLURM_HOME/nginx+latest.sqsh -- nginx -V',
            # Verify absolute path worked
            f'sky logs {name} 1 --status',
            f'sky exec {name} whoami | grep root',
            # Test 2: Relative path - must use ./ prefix for pyxis
            f'SLURM_CLUSTER=$({get_slurm_cluster}) && '
            f'sky launch -y -c {name_rel} --infra slurm/$SLURM_CLUSTER '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--image-id docker:./nginx+latest.sqsh -- nginx -V',
            # Verify relative path worked
            f'sky logs {name_rel} 1 --status',
            f'sky exec {name} whoami | grep root',
        ],
        f'sky down -y {name} {name_rel}; '
        f'SLURM_CLUSTER=$({get_slurm_cluster}) && '
        f'ssh -F ~/.slurm/config $SLURM_CLUSTER "rm -f ~/nginx+latest.sqsh"',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Submitting multiple tasks to the same cluster. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_shadeform  # Shadeform does not have T4 GPUs
@pytest.mark.no_fluidstack  # FluidStack DC has low availability of T4 GPUs
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have T4 gpus
@pytest.mark.no_paperspace  # Paperspace does not have T4 gpus
@pytest.mark.no_ibm  # IBM Cloud does not have T4 gpus
@pytest.mark.no_scp  # SCP does not have T4 gpus
@pytest.mark.no_oci  # OCI Cloud does not have T4 gpus
@pytest.mark.no_do  # DO does not have T4 gpus
@pytest.mark.no_nebius  # Nebius does not have T4 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.no_seeweb  # Seeweb does not have T4 gpus
@pytest.mark.resource_heavy
def test_multi_echo(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    use_spot = True
    accelerator = 'T4'
    if generic_cloud in ('kubernetes', 'slurm'):
        # Slurm and Kubernetes do not support spot instances
        use_spot = False
        accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud)
        if not accelerator:
            pytest.fail(f'No GPUs available for {generic_cloud}.')

    # Determine timeout for 15 running jobs check: 2 min for remote server, single check for local
    is_remote = smoke_tests_utils.is_remote_server_test()

    # Wait loop to check for failures periodically
    wait_for_no_failures = (
        'start_time=$SECONDS; '
        'while true; do '
        's=$(sky queue {cluster_name}); '
        'echo "$s"; '
        'echo; '
        'echo; '
        'if echo "$s" | grep "FAILED"; then '
        '  echo "Found FAILED jobs, exiting"; '
        '  exit 1; '
        'fi; '
        'if (( $SECONDS - $start_time > 70 )); then '
        '  echo "No failures detected after 70 seconds, proceeding"; '
        '  break; '
        'fi; '
        'echo "Waiting and checking for failures..."; '
        'sleep 10; '
        'done').format(cluster_name=name)

    # Check for at least 15 RUNNING jobs
    # Remote server: while loop with 2 min timeout, wait if less than 15 running
    # Local server: single check, exit if less than 15 running
    if is_remote:
        wait_for_15_running = (
            'start_time=$SECONDS; '
            'while true; do '
            's=$(sky queue {cluster_name}); '
            'echo "$s"; '
            'echo; '
            'echo; '
            'running_count=$(echo "$s" | grep "RUNNING" | wc -l); '
            'echo "Current RUNNING jobs: $running_count"; '
            'if [ "$running_count" -eq 0 ]; then '
            '  echo "No running jobs found, exiting"; '
            '  exit 1; '
            'fi; '
            'if [ "$running_count" -ge 15 ]; then '
            '  echo "Found at least 15 RUNNING jobs, proceeding"; '
            '  break; '
            'fi; '
            'if (( $SECONDS - $start_time > 120 )); then '
            '  echo "Timeout after 120 seconds waiting for 15 RUNNING jobs"; '
            '  exit 1; '
            'fi; '
            'echo "Waiting for at least 15 RUNNING jobs (current: $running_count)..."; '
            'sleep 10; '
            'done').format(cluster_name=name)
    else:
        # Local server: single check only
        wait_for_15_running = (
            's=$(sky queue {cluster_name}); '
            'echo "$s"; '
            'echo; '
            'echo; '
            'running_count=$(echo "$s" | grep "RUNNING" | wc -l); '
            'echo "Current RUNNING jobs: $running_count"; '
            'if [ "$running_count" -lt 15 ]; then '
            '  echo "Found less than 15 RUNNING jobs ($running_count), exiting"; '
            '  exit 1; '
            'fi; '
            'echo "Found at least 15 RUNNING jobs, proceeding"; ').format(
                cluster_name=name)

    # Build commands list - skip API server restart for remote server tests
    commands = []
    if not is_remote:
        # TODO(aylei): also test local API server after we have rate limit in local mode
        # Use deploy mode to avoid ulimited concurrency requests exhausts the CPU
        commands.append('sky api stop && sky api start --deploy')

    commands.extend([
        f'python examples/multi_echo.py {name} {generic_cloud} {int(use_spot)} {accelerator}',
        wait_for_no_failures,
        # Make sure that our job scheduler is fast enough to have at least
        # 15 RUNNING jobs in parallel.
        wait_for_15_running,
        # Final check for failures
        f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep "FAILED" && exit 1 || true',
        # This is to make sure we can finish job 32 before the test timeout.
        f'until sky logs {name} 32 --status; do echo "Waiting for job 32 to finish..."; sleep 10; done',
    ])

    test = smoke_tests_utils.Test(
        'multi_echo',
        commands +
        # Ensure jobs succeeded.
        [
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id=i + 1,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120) for i in range(32)
        ] + [
            # ssh record will only be created on cli command like sky status on client side.
            f'sky status {name}',
            # Ensure monitor/autoscaler didn't crash on the 'assert not
            # unfulfilled' error.  If process not found, grep->ssh returns 1.
            f'ssh {name} \'ps aux | grep "[/]"monitor.py\''
        ],
        (f'sky down -y {name}' if is_remote else
         f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api stop && '
         f'{skypilot_config.ENV_VAR_GLOBAL_CONFIG}=${skypilot_config.ENV_VAR_GLOBAL_CONFIG}_ORIGINAL sky api start; '
         f'sky down -y {name}'),
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Task: 1 node training. ----------
@pytest.mark.no_vast  # Vast has low availability of T4 GPUs
@pytest.mark.no_shadeform  # Shadeform does not have T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack does not have T4 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_scp  # SCP does not have T4 gpus. Run test_scp_huggingface instead.
@pytest.mark.no_hyperbolic  # Hyperbolic has low availability of T4 GPUs
@pytest.mark.no_seeweb  # Seeweb does not support T4 GPUs
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_huggingface(generic_cloud: str, accelerator: Dict[str, str]):
    if generic_cloud in ('kubernetes', 'slurm'):
        accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud)
        if not accelerator:
            pytest.fail(f'No GPUs available for {generic_cloud}.')
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} --gpus {accelerator} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_huggingface_arm64(generic_cloud: str):
    accelerator = 'T4g'
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'huggingface_glue_imdb_app_arm',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/huggingface_glue_imdb_app_arm.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} --gpus {accelerator} examples/huggingface_glue_imdb_app_arm.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.lambda_cloud
def test_lambda_huggingface(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'lambda_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LAMBDA_TYPE} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {smoke_tests_utils.LAMBDA_TYPE} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_huggingface(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    num_of_gpu_launch = 1
    test = smoke_tests_utils.Test(
        'SCP_huggingface_glue_imdb_app',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} {smoke_tests_utils.SCP_TYPE} {smoke_tests_utils.SCP_GPU_V100}:{num_of_gpu_launch} examples/huggingface_glue_imdb_app.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Inferentia. ----------
@pytest.mark.aws
def test_inferentia():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_inferentia',
        [
            f'sky launch -y -c {name} -t inf2.xlarge -- echo hi',
            f'sky exec {name} --gpus Inferentia2:1 echo hi',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- TPU VM. ----------
@pytest.mark.skip(reason='We are having trouble getting TPUs in GCP.')
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
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
    smoke_tests_utils.run_one_test(test)


# ---------- TPU VM Pod. ----------
@pytest.mark.skip(reason='We are having trouble getting TPUs in GCP.')
@pytest.mark.gcp
@pytest.mark.tpu
def test_tpu_vm_pod():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'tpu_pod',
        [
            f'sky launch -y -c {name} examples/tpu/tpuvm_mnist.yaml --gpus tpu-v2-32 --use-spot --zone europe-west4-a',
            f'sky logs {name} 1',  # Ensure the job finished.
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # can take 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- TPU Pod Slice on GKE. ----------
@pytest.mark.kubernetes
@pytest.mark.skip
def test_tpu_pod_slice_gke():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
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
    smoke_tests_utils.run_one_test(test)


# ---------- Simple apps. ----------
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
def test_multi_hostname(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'multi_hostname',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/multi_hostname.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 1 | grep "My hostname:" | wc -l | grep 2',  # Ensure there are 2 hosts.
            f'sky exec {name} examples/multi_hostname.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud,
                                              total_timeout_minutes * 60),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support multi-node
def test_multi_node_failure(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multi_node_failure',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/failed_worker_setup.yaml || [ $? -eq 100 ]',
            f'sky logs {name} 1 --status | grep FAILED_SETUP',  # Ensure the job setup failed.
            f'sky exec {name} tests/test_yamls/failed_worker_run.yaml || [ $? -eq 100 ]',
            f'sky logs {name} 2 --status | grep FAILED',  # Ensure the job failed.
            f'sky logs {name} 2 | grep "My hostname:" | wc -l | grep 2',  # Ensure there 2 of the hosts printed their hostname.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- EFA. ----------
@pytest.mark.aws
def test_efa():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'efa',
        [
            f'sky launch -y -c {name} --infra aws/ap-northeast-1 --gpus L4:1 --instance-type g6.8xlarge examples/aws_efa/efa_vm.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky logs {name} 1 | grep "Selected provider is efa, fabric is efa"',  # Ensure efa is enabled.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on GCP. ----------
@pytest.mark.gcp
def test_gcp_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra gcp {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on AWS. ----------
@pytest.mark.aws
def test_aws_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra aws {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Azure. ----------
@pytest.mark.azure
def test_azure_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra azure {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Kubernetes. ----------
@pytest.mark.kubernetes
@pytest.mark.no_remote_server
def test_kubernetes_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'kubernetes_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra kubernetes examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 100); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 5; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on Paperspace. ----------
@pytest.mark.paperspace
def test_paperspace_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'paperspace_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --infra paperspace examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on RunPod. ----------
@pytest.mark.runpod
def test_runpod_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'runpod_http_server_with_custom_ports',
        [
            # RunPod CPU instances have a maximum local disk size limit of 10x number of vCPUs.
            f'sky launch -y -d -c {name} --infra runpod --disk-size 20 examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Web apps with custom ports on SCP. ----------
@pytest.mark.scp
def test_scp_http_server_with_custom_ports():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'scp_http_server_with_custom_ports',
        [
            f'sky launch -y -d -c {name} --cloud scp {smoke_tests_utils.LOW_RESOURCE_ARG} examples/http_server_with_custom_ports/task.yaml',
            f'until SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}; do sleep 10; done',
            # Retry a few times to avoid flakiness in ports being open.
            f'ip=$(SKYPILOT_DEBUG=0 sky status --endpoint 33828 {name}); success=false; for i in $(seq 1 5); do if curl $ip | grep "<h1>This is a demo HTML page.</h1>"; then success=true; break; fi; sleep 10; done; if [ "$success" = false ]; then exit 1; fi'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on AWS (instance_tags) ----------
@pytest.mark.aws
def test_task_labels_aws():
    if smoke_tests_utils.is_remote_server_test():
        pytest.skip('Skipping test_task_labels on remote server')
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='aws', region='us-east-1')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_aws',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with aws cli that the tags are set.
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, 'aws ec2 describe-instances '
                    '--query "Reservations[*].Instances[*].InstanceId" '
                    '--filters "Name=instance-state-name,Values=running" '
                    f'--filters "Name=tag:skypilot-cluster-name,Values={name}*" '
                    '--filters "Name=tag:inlinelabel1,Values=inlinevalue1" '
                    '--filters "Name=tag:inlinelabel2,Values=inlinevalue2" '
                    '--region us-east-1 --output text'),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on GCP (labels) ----------
@pytest.mark.gcp
def test_task_labels_gcp():
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='gcp')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_gcp',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with gcloud cli that the tags are set
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    cmd=
                    (f'gcloud compute instances list --filter="name~\'^{name}\' AND '
                     'labels.inlinelabel1=\'inlinevalue1\' AND '
                     'labels.inlinelabel2=\'inlinevalue2\'" '
                     '--format="value(name)" | grep .')),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Labels from task on Kubernetes (labels) ----------
@pytest.mark.kubernetes
def test_task_labels_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_labels.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(cloud='kubernetes')
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        test = smoke_tests_utils.Test(
            'task_labels_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} {file_path}',
                # Verify with kubectl that the labels are set.
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, 'kubectl get pods '
                    '--selector inlinelabel1=inlinevalue1 '
                    '--selector inlinelabel2=inlinevalue2 '
                    '-o jsonpath=\'{.items[*].metadata.name}\' | '
                    f'grep \'^{name}\'')
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Services on Kubernetes ----------
@pytest.mark.kubernetes
def test_services_on_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    service_check = smoke_tests_utils.run_cloud_cmd_on_cluster(
        name,
        f'services=$(kubectl get svc -o name | grep -F {name} | grep -v -- "-cloud-cmd" || true); '
        'echo "[$services]"; '
        'if [ -n "$services" ]; then echo "services found"; exit 1; else echo "services not found"; fi'
    )
    test = smoke_tests_utils.Test(
        'services_on_kubernetes',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch Kubernetes cluster with three nodes.
            f'sky launch -y -c {name} --num-nodes 3 --cpus=0.1+ --infra kubernetes',
        ],
        f'sky down -y {name} && {service_check} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Pod Annotations on Kubernetes ----------
@pytest.mark.kubernetes
def test_add_pod_annotations_for_autodown_with_launch():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'add_pod_annotations_for_autodown_with_launch',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            # Autodown is set.
            f'sky launch -y -c {name} -i 10 --down --num-nodes 2 --cpus=1 --infra kubernetes',
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                # Describe the first pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                # Describe the second pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
        ],
        f'sky down -y {name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_add_and_remove_pod_annotations_with_autostop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'add_and_remove_pod_annotations_with_autostop',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch Kubernetes cluster with two nodes, each being head node and worker node.
            f'sky launch -y -c {name} --num-nodes 2 --cpus=1 --infra kubernetes',
            # Set autodown on the cluster with 'autostop' command.
            f'sky autostop -y {name} -i 20 --down',
            # Get names of the pods containing cluster name.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                # Describe the first pod and check for annotations.
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
            # Describe the second pod and check for annotations.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop'
            ),
            # Cancel the set autodown to remove the annotations from the pods.
            f'sky autostop -y {name} --cancel',
            # Describe the first pod and check if annotations are removed.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_1=$(kubectl get pods -o name | grep {name} | sed -n 1p) && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_1); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
            # Describe the second pod and check if annotations are removed.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod_2=$(kubectl get pods -o name | grep {name} | sed -n 2p) && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/autodown && '
                'pod_tag=$(kubectl describe $pod_2); echo "$pod_tag"; ! echo "$pod_tag" | grep -q skypilot.co/idle_minutes_to_autostop',
            ),
        ],
        f'sky down -y {name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Volumes on Kubernetes ----------
@pytest.mark.kubernetes
def test_volumes_on_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'volumes_on_kubernetes',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'kubectl delete pvc existing0 --ignore-not-found && '
                f'kubectl create -f - <<EOF\n'
                f'apiVersion: v1\n'
                f'kind: PersistentVolumeClaim\n'
                f'metadata:\n'
                f'  name: existing0\n'
                f'spec:\n'
                f'  accessModes:\n'
                f'    - ReadWriteOnce\n'
                f'  resources:\n'
                f'    requests:\n'
                f'      storage: 1Gi\n'
                f'EOF',
            ),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, 'end=$((SECONDS+60)); '
                'while [ $SECONDS -lt $end ]; do '
                'if kubectl get pvc existing0; then exit 0; fi; '
                'sleep 1; '
                'done; exit 1'),
            f'sky volumes apply -y -n pvc0 --type k8s-pvc --size 2GB',
            f'sky volumes apply -y -n existing0 --type k8s-pvc --size 2GB --use-existing',
            f'vols=$(sky volumes ls) && echo "$vols" && echo "$vols" | grep "pvc0" && echo "$vols" | grep "existing0"',
            f'sky launch -y -c {name} --infra kubernetes tests/test_yamls/pvc_volume.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'vols=$(sky volumes ls) && echo "$vols" && echo "$vols" | grep "{name}"',
            # Test volume mounting warning on relaunch with new volume
            # Create a new volume pvc1
            f'sky volumes apply -y -n pvc1 --type k8s-pvc --size 2GB',
            # Launch with the new volume - should show warning that pvc1 and /mnt/data4 won't be mounted
            f's=$(sky launch -y -c {name} --infra kubernetes tests/test_yamls/pvc_volume_with_new.yaml 2>&1 | tee /dev/stderr) && echo "$s" | grep -i "WARNING: New ephemeral volume(s) with path /mnt/data4 and new volume(s) pvc1 specified in task but not mounted"',
            f'sky logs {name} 2 --status',  # Ensure the second job succeeded.
            f'sky down -y {name} && sky volumes ls && sky volumes delete pvc0 existing0 pvc1 -y',
            f'vols=$(sky volumes ls) && echo "$vols" && vol=$(echo "$vols" | grep "pvc0"); if [ -n "$vol" ]; then echo "pvc0 not deleted" && exit 1; else echo "pvc0 deleted"; fi',
            f'vols=$(sky volumes ls) && echo "$vols" && vol=$(echo "$vols" | grep "existing0"); if [ -n "$vol" ]; then echo "existing0 not deleted" && exit 1; else echo "existing0 deleted"; fi',
            f'vols=$(sky volumes ls) && echo "$vols" && vol=$(echo "$vols" | grep "pvc1"); if [ -n "$vol" ]; then echo "pvc1 not deleted" && exit 1; else echo "pvc1 deleted"; fi',
            f'vols=$(sky volumes ls) && echo "$vols" && vol=$(echo "$vols" | grep "{name}"); if [ -n "$vol" ]; then echo "ephemeral volume for cluster {name} not deleted" && exit 1; else echo "ephemeral volume for cluster {name} deleted"; fi',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                'pvcs=$(kubectl get pvc) && echo "$pvcs" && pvc=$(echo "$pvcs" | grep "pvc0"); if [ -n "$pvc" ]; then echo "pvc for volume pvc0 not deleted" && exit 1; else echo "pvc for volume pvc0 deleted"; fi && '
                'pvc=$(echo "$pvcs" | grep "existing0"); if [ -n "$pvc" ]; then echo "pvc for volume existing0 not deleted" && exit 1; else echo "pvc for volume existing0 deleted"; fi && '
                'pvc=$(echo "$pvcs" | grep "pvc1"); if [ -n "$pvc" ]; then echo "pvc for volume pvc1 not deleted" && exit 1; else echo "pvc for volume pvc1 deleted"; fi && '
                f'pvc=$(echo "$pvcs" | grep "{name}"); if [ -n "$pvc" ]; then echo "pvc for ephemeral volume of cluster {name} not deleted" && exit 1; else echo "pvc for ephemeral volume of cluster {name} deleted"; fi',
            ),
        ],
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)} && vols=$(sky volumes ls) && echo "$vols" && vol=$(echo "$vols" | grep "existing0"); if [ -n "$vol" ]; then sky volumes delete existing0 -y; fi && vol=$(echo "$vols" | grep "pvc0"); if [ -n "$vol" ]; then sky volumes delete pvc0 -y; fi && vol=$(echo "$vols" | grep "pvc1"); if [ -n "$vol" ]; then sky volumes delete pvc1 -y; fi',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_volume_env_mount_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    pvc_name = f'{name}-pvc'
    mount_job_conf = textwrap.dedent(f"""
        name: {name}-job
        volumes:
          /mnt/test-data: ${{USERNAME}}-{pvc_name}
        run: |
          echo "Mounted volume"
    """)
    full_pvc_name = f'user-{pvc_name}'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(mount_job_conf)
        f.flush()
        test = smoke_tests_utils.Test(
            'volume_env_mount_kubernetes',
            [
                f'sky volumes apply -y -n {full_pvc_name} --type k8s-pvc --size 2GB',
                f's=$(sky jobs launch -y --infra kubernetes {f.name} --env USERNAME=user); echo "$s"; echo "$s" | grep "Job finished (status: SUCCEEDED)"',
            ],
            ' && '.join([
                'sky jobs cancel -a -y || true', 'sleep 5',
                f'sky volumes delete {full_pvc_name} -y',
                f'(vol=$(sky volumes ls | grep "{full_pvc_name}"); '
                f'if [ -n "$vol" ]; then echo "{full_pvc_name} not deleted" '
                '&& exit 1; else echo "{full_pvc_name} deleted"; fi)'
            ]),
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Container logs from task on Kubernetes ----------


def _check_container_logs(name, logs, total_lines, count, timeout=60):
    """Check if the container logs contain the expected number of logging lines.

    Each line should be only one number in the given range and should show up
    count number of times. We skip the messages that we see in the job from
    running setup with set -x.

    This function includes a retry mechanism because there can be a small delay
    between job completion and when container logs become fully available via
    kubectl logs.
    """
    # The awk script checks if each number from 1 to total_lines appears
    # exactly 'count' times and that no other numbers are present.
    awk_check = f"""awk '
  /^[0-9]+$/ {{ counts[$0]++ }}
  END {{
    if (length(counts) != {total_lines}) {{
      exit 1
    }}
    for (i = 1; i <= {total_lines}; i++) {{
      if (counts[i] != {count}) {{
        exit 1
      }}
    }}
    exit 0
  }}'"""

    # Wrap in a retry loop with timeout
    output_cmd = f'''
start_time=$SECONDS
while true; do
    if (( $SECONDS - start_time > {timeout} )); then
        echo "Timeout after {timeout} seconds waiting for container logs"
        exit 1
    fi
    s=$({logs})
    if echo "$s" | {awk_check}; then
        echo "Container logs verified successfully"
        break
    fi
    echo "Waiting for container logs to be ready..."
    sleep 5
done
'''
    return smoke_tests_utils.run_cloud_cmd_on_cluster(
        name,
        output_cmd,
    )


@pytest.mark.kubernetes
def test_container_logs_multinode_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    head_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} | '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head | '
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    worker_logs = ('all_pods=$(kubectl get pods); echo "$all_pods"; '
                   f'echo "$all_pods" | grep {name} |  grep worker | '
                   " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'container_logs_multinode_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} --infra kubernetes {task_yaml} --num-nodes 2',
                _check_container_logs(name, head_logs, 9, 1),
                _check_container_logs(name, worker_logs, 9, 1),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_jobs_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml'
    pod_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} | '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head |'
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'test_container_logs_two_jobs_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} --infra kubernetes {task_yaml}',
                f'sky launch -y -c {name} --infra kubernetes {task_yaml}',
                _check_container_logs(name, pod_logs, 9, 2),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_container_logs_two_simultaneous_jobs_kubernetes():
    name = smoke_tests_utils.get_cluster_name()
    task_yaml = 'tests/test_yamls/test_k8s_logs.yaml '
    pod_logs = (
        'all_pods=$(kubectl get pods); echo "$all_pods"; '
        f'echo "$all_pods" | grep {name} |  '
        # Exclude the cloud cmd execution pod.
        'grep -v "cloud-cmd" |  '
        'grep head |'
        " awk '{print $1}' | xargs -I {} kubectl logs {}")
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test = smoke_tests_utils.Test(
            'test_container_logs_two_simultaneous_jobs_kubernetes',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                f'sky launch -y -c {name} --infra kubernetes',
                f'sky exec -c {name} -d {task_yaml}',
                f'sky exec -c {name} -d {task_yaml}',
                'sleep 30',
                _check_container_logs(name, pod_logs, 9, 2),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Task: n=2 nodes with setups. ----------
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_do  # DO does not have V100 gpus
@pytest.mark.no_nebius  # Nebius does not have V100 gpus
@pytest.mark.no_hyperbolic  # Hyperbolic does not have V100 gpus
@pytest.mark.no_seeweb  # Seeweb does not have V100 gpus
@pytest.mark.skip(
    reason=
    'The resnet_distributed_tf_app is flaky, due to it failing to detect GPUs.')
def test_distributed_tf(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            f'python examples/resnet_distributed_tf_app.py {name} {generic_cloud}',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing GCP start and stop instances ----------
@pytest.mark.gcp
def test_gcp_start_stop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp-start-stop',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/gcp_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=40),
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/gcp_start_stop.yaml',
            f'sky logs {name} 4 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.STOPPED, sky.ClusterStatus.INIT
                ],
                timeout=200),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Azure start and stop instances ----------
@pytest.mark.azure
def test_azure_start_stop():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-start-stop',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/azure_start_stop.yaml',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',  # Ensure the raylet process has the correct file descriptor limit.
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.STOPPED, sky.ClusterStatus.INIT
                ],
                timeout=280) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Autostopping ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 and autostop yet
@pytest.mark.no_seeweb  # Seeweb does not support autostop
@pytest.mark.no_slurm  # Slurm does not support autostop yet
def test_autostop_wait_for_jobs(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    # Launching and starting Azure clusters can take a long time too. e.g., restart
    # a stopped Azure cluster can take 7m. So we set the total timeout to 70m.
    total_timeout_minutes = 70 if generic_cloud == 'azure' else 20
    test = smoke_tests_utils.Test(
        'autostop_wait_for_jobs',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} -i 1 --wait-for jobs',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the cluster is not stopped early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',

            # Ensure the cluster is STOPPED.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),

            # Ensure the cluster is UP.
            # Change the autostop setting to be very high so we can test
            # resetting it.
            f'sky start -y {name} -i 500',
            f'sky status | grep {name} | grep "UP"',

            # Ensure the job succeeded.
            f'sky exec {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',

            # Test restarting the idleness timer via reset:
            f'sky autostop -y {name} -i 1 --wait-for jobs',  # Idleness starts counting.
            'sleep 40',  # Almost reached the threshold.
            f'sky autostop -y {name} -i 1 --wait-for jobs',  # Should restart the timer.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s" | grep {name} | grep UP',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),

            # Test restarting the idleness timer via exec:
            # Change the autostop setting to be very high so we can test
            # resetting it.
            f'sky start -y {name} -i 500',
            f'sky status | grep {name} | grep "UP"',
            f'sky autostop -y {name} -i 1 --wait-for jobs',  # Idleness starts counting.
            'sleep 45',  # Almost reached the threshold.
            f'sky exec {name} echo hi',  # Should restart the timer.
            'sleep 45',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 and autostop yet
@pytest.mark.no_seeweb  # Seeweb does not support autostop
@pytest.mark.no_slurm  # Slurm does not support autostop yet
def test_autostop_wait_for_jobs_and_ssh(generic_cloud: str):
    """Test that autostop is prevented when SSH sessions are active."""
    name = smoke_tests_utils.get_cluster_name()
    # See test_autostop_wait_for_jobs() for explanation of autostop_timeout.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250

    test = smoke_tests_utils.Test(
        'autostop_wait_for_jobs_and_ssh',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            # --wait-for jobs_and_ssh is the default, so we don't need to specify it here.
            f'sky autostop -y {name} -i 1',

            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m"',

            # Ensure the job succeeded.
            f'sky queue {name} | grep SUCCEEDED',

            # Start an interactive SSH session to keep the cluster active.
            # -tt forces a pseudo-terminal to be allocated.
            f'ssh -tt {name} "sleep 180"',

            # Ensure the cluster is still UP (autostop should be prevented by active SSH session).
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s" | grep {name} | grep UP',

            # Now the cluster should autostop since no SSH sessions are active
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_ibm  # FIX(IBM) sporadically fails, as restarted workers stay uninitialized indefinitely
@pytest.mark.no_scp  # 180s does not enough
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 and autostop yet
@pytest.mark.no_seeweb  # Seeweb does not support autostop
@pytest.mark.no_slurm  # Slurm does not support autostop yet
def test_autostop_wait_for_none(generic_cloud: str):
    """Test that autostop is prevented when hard stop is set."""
    name = smoke_tests_utils.get_cluster_name()
    # See test_autostop_wait_for_jobs() for explanation of autostop_timeout.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250

    test = smoke_tests_utils.Test(
        'autostop_with_hard_stop',
        [
            # Launch a cluster with a long running job (1h).
            f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} sleep 3600 --async',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.UP],
                timeout=180),

            # Set wait mode to none, so the cluster doesn't wait for the job to finish.
            f'sky autostop -y {name} -i 1 --wait-for none',
            f'sky status | grep {name} | grep "1m"',

            # The cluster should autostop.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def _get_cancel_task_with_cloud(name, cloud, timeout=15 * 60):
    test = smoke_tests_utils.Test(
        f'{cloud}-cancel-task',
        [
            f'sky launch -c {name} examples/resnet_app.yaml --infra {cloud} -y -d',
            # Wait the job to be scheduled and finished setup.
            f'until sky queue {name} | grep "RUNNING"; do sleep 10; done',
            # Wait the setup and initialize before the GPU process starts.
            'sleep 120',
            f'sky exec {name} "nvidia-smi | grep python"',
            f'sky logs {name} 2 --status || {{ sky logs {name} --no-follow 1 && exit 1; }}',  # Ensure the job succeeded.
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
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'aws')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_cancel_gcp():
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'gcp')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_cancel_azure():
    name = smoke_tests_utils.get_cluster_name()
    test = _get_cancel_task_with_cloud(name, 'azure', timeout=30 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # Fluidstack does not support V100 gpus for now
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not have V100 gpus
@pytest.mark.no_ibm  # IBM cloud currently doesn't provide public image with CUDA
@pytest.mark.no_paperspace  # Paperspace has `gnome-shell` on nvidia-smi
@pytest.mark.no_scp  # SCP does not have T4 gpus
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support num_nodes > 1 yet
@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'H100'}])
def test_cancel_pytorch(generic_cloud: str, accelerator: Dict[str, str]):
    if generic_cloud in ('kubernetes', 'slurm'):
        accelerator = smoke_tests_utils.get_available_gpus(infra=generic_cloud)
        if not accelerator:
            pytest.fail(f'No GPUs available for {generic_cloud}.')
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cancel-pytorch',
        [
            f'sky launch -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus {accelerator} examples/resnet_distributed_torch.yaml -y -d',
            # Wait until the setup finishes.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=name,
                job_id='1',
                job_status=[sky.JobStatus.RUNNING, sky.JobStatus.SUCCEEDED],
                timeout=150),
            # Wait the GPU process to start.
            'sleep 90',
            f'sky exec {name} --num-nodes 2 \'s=$(nvidia-smi); echo "$s"; echo "$s" | grep python || '
            # When run inside container/k8s, nvidia-smi cannot show process ids.
            # See https://github.com/NVIDIA/nvidia-docker/issues/179
            # To work around, we check if GPU utilization is greater than 0.
            f'[ $(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -gt 0 ]\'',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            f'sky cancel -y {name} 1',
            'sleep 60',
            f'sky exec {name} --num-nodes 2 \'s=$(nvidia-smi); echo "$s"; (echo "$s" | grep "No running process") || '
            # Ensure Xorg is the only process running.
            '[ $(nvidia-smi | grep -A 10 Processes | grep -A 10 === | grep -v Xorg) -eq 2 ]\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# can't use `_get_cancel_task_with_cloud()`, as command `nvidia-smi`
# requires a CUDA public image, which IBM doesn't offer
@pytest.mark.ibm
def test_cancel_ibm():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'ibm-cancel-task',
        [
            f'sky launch -y -c {name} --infra ibm examples/minimal.yaml',
            f'sky exec {name} -n {name}-1 -d  "while true; do echo \'Hello SkyPilot\'; sleep 2; done"',
            'sleep 20',
            f'sky queue {name} | grep {name}-1 | grep RUNNING',
            f'sky cancel -y {name} 2',
            f'sleep 5',
            f'sky queue {name} | grep {name}-1 | grep CANCELLED',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing use-spot option ----------
@pytest.mark.no_fluidstack  # FluidStack does not support spot instances
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support spot instances
@pytest.mark.no_paperspace  # Paperspace does not support spot instances
@pytest.mark.no_ibm  # IBM Cloud does not support spot instances
@pytest.mark.no_scp  # SCP does not support spot instances
@pytest.mark.no_kubernetes  # Kubernetes does not have a notion of spot instances
@pytest.mark.no_slurm  # Slurm does not have a notion of spot instances
@pytest.mark.no_nebius  # Nebius does not support non-GPU spot instances
@pytest.mark.no_hyperbolic  # Hyperbolic does not support spot instances
@pytest.mark.no_shadeform  # Shadeform does not support spot instances
@pytest.mark.no_seeweb  # Seeweb does not support spot instances
@pytest.mark.no_do
def test_use_spot(generic_cloud: str):
    """Test use-spot and sky exec."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'use-spot',
        [
            f'sky launch -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status',
            f'sky exec {name} echo hi',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_spot_instance_verification():
    """Test Azure spot instance provisioning with explicit verification.
    This test verifies that when --use-spot is specified for Azure:
    1. The cluster launches successfully
    2. The instances are actually provisioned as spot instances
    """
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-spot-verification',
        [
            f'sky launch -c {name} --infra azure {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml --use-spot -y',
            f'sky logs {name} 1 --status', f'TARGET_VM_NAME="{name}"; '
            'VM_INFO=$(az vm list --query "[?contains(name, \'$TARGET_VM_NAME\')].{Name:name, ResourceGroup:resourceGroup}" -o tsv); '
            '[[ -z "$VM_INFO" ]] && exit 1; '
            'FULL_VM_NAME=$(echo "$VM_INFO" | awk \'{print $1}\'); '
            'RESOURCE_GROUP=$(echo "$VM_INFO" | awk \'{print $2}\'); '
            'VM_DETAILS=$(az vm list --resource-group "$RESOURCE_GROUP" '
            '--query "[?name==\'$FULL_VM_NAME\'].{Name:name, Location:location, Priority:priority}" -o table); '
            '[[ -z "$VM_DETAILS" ]] && exit 1; '
            'echo "VM Details:"; echo "$VM_DETAILS"; '
            'echo "$VM_DETAILS" | grep -qw "Spot" && exit 0 || exit 1'
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_stop_gcp_spot():
    """Test GCP spot can be stopped, autostopped, restarted."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'stop_gcp_spot',
        [
            f'sky launch -c {name} --infra gcp {smoke_tests_utils.LOW_RESOURCE_ARG} --use-spot -y -- touch myfile',
            # stop should go through:
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 2 --status',
            f'sky autostop {name} -i0 -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=90),
            f'sky start {name} -y',
            f'sky exec {name} -- ls myfile',
            f'sky logs {name} 3 --status',
            # -i option at launch should go through:
            f'sky launch -c {name} -i0 -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=120),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env ----------
def test_inline_env(generic_cloud: str):
    """Test env"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-inline-env',
        [
            f'sky launch -c {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            'sleep 20',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env file ----------
@pytest.mark.no_hyperbolic  # Hyperbolic fails to provision resources
def test_inline_env_file(generic_cloud: str):
    """Test env"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-inline-env-file',
        [
            f'sky launch -c {name} -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 1 --status',
            f'sky exec {name} --env-file examples/sample_dotenv "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_IPS}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NODE_RANK}\\" ]] && [[ ! -z \\"\${constants.SKYPILOT_NUM_NODES}\\" ]]) || exit 1"',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing custom image ----------
@pytest.mark.aws
def test_aws_custom_image():
    """Test AWS custom image"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-aws-custom-image',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --infra aws/us-east-2 --image-id ami-062ddd90fb6f8267a',  # Nvidia image
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.parametrize(
    'image_id',
    [
        'docker:verlai/verl:sgl055.latest',
        # 'docker:nvcr.io/nvidia/quantum/cuda-quantum:cu12-0.10.0',
    ])
def test_aws_custom_docker_image_with_motd(image_id):
    """Test AWS custom image with MOTD contamination"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-aws-custom-image',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --infra aws --image-id {image_id}',
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.resource_heavy
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
        # Test image with custom MOTD that can potentially interfere with
        # SSH user/rsync path detection.
        'docker:nvcr.io/nvidia/quantum/cuda-quantum:cu12-0.10.0',
        # Test image with PYTHONPATH set and with pyproject.toml.
        # Update this image periodically, nemo does not support :latest tag.
        'docker:nvcr.io/nvidia/nemo:25.09',
        # Test image with Python 3.12 site-packages as WORKDIR, which causes
        # import failures if CWD is not handled properly. When SkyPilot's Python
        # 3.10 venv runs, it finds Python 3.12 compiled packages (like rpds) in
        # CWD first, causing "ModuleNotFoundError: No module named 'rpds.rpds'".
        # Created with:
        # FROM python:3.12-slim
        # RUN pip install jsonschema
        # WORKDIR /usr/local/lib/python3.12/site-packages
        'docker:michaelvll/skypilot-custom-image-test-cases:py312-site-packages-workdir-v1'
    ])
def test_kubernetes_custom_image(image_id):
    """Test Kubernetes custom image"""
    accelerator = smoke_tests_utils.get_available_gpus()
    name = smoke_tests_utils.get_cluster_name()
    gpus_arg = f'{accelerator}:1' if accelerator else 'none'
    test = smoke_tests_utils.Test(
        'test-kubernetes-custom-image',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --retry-until-up -y tests/test_yamls/test_custom_image.yaml --infra kubernetes/none --image-id {image_id} --gpus {gpus_arg}',
            f'sky logs {name} 1 --status',
            # Try exec to run again and check if the logs are printed
            f'sky exec {name} tests/test_yamls/test_custom_image.yaml --infra kubernetes/none --image-id {image_id} --gpus {gpus_arg} | grep "Hello 100"',
            # Make sure ssh is working with custom username
            f'ssh {name} echo hi | grep hi',
        ],
        f'sky down -y {name}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_pod_failure_detection():
    """Test that we detect pod failures and log useful details.

    We use busybox image because it doesn't have bash,
    so we know the pod must fail.
    """
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test-kubernetes-pod-failure-detection',
        [
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} -y --image-id docker:busybox:latest --infra kubernetes echo hi || true',
            # Check that the provision logs contain the expected error message.
            f's=$(sky logs --provision {name}) && echo "==Validating error message==" && echo "$s" && echo "$s" | grep -A 2 "Pod.*terminated:.*" | grep -A 2 "PodFailed" | grep "StartError"',
        ],
        f'sky down -y {name}',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.resource_heavy  # Not actually resource heavy, but can't reproduce on kind clusters.
@pytest.mark.no_auto_retry
def test_kubernetes_container_status_unknown_status_refresh():
    """Test sky status --refresh handles evicted pods without crashing.

    When pods are evicted due to ephemeral storage limits, containers may enter
    ContainerStatusUnknown state with terminated.finishedAt=null. This test
    verifies that SkyPilot handles evicted pods without erroring.

    Note: This test is inherently flaky, it may succeed even before the fix.
    Triggering ContainerStatusUnknown (where finishedAt is null) requires the kubelet
    to lose contact with the container runtime during eviction, which is racy. The pod
    may instead get a clean termination with finishedAt set.

    Regression test for #8674.
    """
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'kubernetes_container_status_unknown_status_refresh',
        [
            f'sky launch -y -c {name} --infra kubernetes --num-nodes 8 --detach-run tests/test_yamls/test_k8s_ephemeral_storage_eviction.yaml',
            # Poll sky status --refresh, fail fast if error found.
            # Before the fix this logged: "Failed to query ... [TypeError]..."
            (f'for i in $(seq 1 20); do '
             f'echo "=== status refresh attempt $i ===" && '
             f'OUT=$(sky status {name} -v --refresh 2>&1) && '
             f'echo "$OUT" && '
             f'if echo "$OUT" | grep -q "TypeError"; then '
             f'echo "FAIL: TypeError found" && exit 1; fi && '
             f'if echo "$OUT" | grep -q "Failed to refresh status"; then '
             f'echo "FAIL: Refresh failed" && exit 1; fi; done'),
        ],
        f'sky down -y {name}',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_pod_pending_reason():
    """Ensure pending pod reasons are surfaced in provision logs."""
    name = smoke_tests_utils.get_cluster_name()
    template_str = pathlib.Path(
        'tests/test_yamls/test_k8s_pending_volume.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    task_yaml_content = template.render()

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(task_yaml_content)
        f.flush()

        # This launch will be stuck in pending, so launch
        # in background and kill after timeout.
        launch_with_timeout_cmd = (
            f'sky launch -y -c {name} --infra kubernetes {f.name} & '
            f'LAUNCH_PID=$!; '
            f'sleep 60; '
            f'kill $LAUNCH_PID 2>/dev/null || true; '
            f'wait $LAUNCH_PID 2>/dev/null || true')

        test = smoke_tests_utils.Test(
            'kubernetes_pod_pending_reason',
            [
                launch_with_timeout_cmd,
                f's=$(sky logs --provision {name} --no-follow 2>&1); echo "$s"; echo; '
                f'echo "$s" | grep "is pending: FailedMount: MountVolume.SetUp failed for volume"'
            ],
            f'sky down -y {name}',
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_pod_long_image_pull():
    """Ensure kubelet image pulling events are surfaced in provision logs."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'kubernetes_pod_long_image_pull',
        [
            # Use a large image, for example CUDA runtime.
            f'sky launch -y -c {name} --infra kubernetes --num-nodes 3 --image-id docker:nvidia/cuda:13.0.1-runtime-ubuntu24.04',
            f's=$(sky logs --provision {name} --no-follow 2>&1); echo "$s"; echo; '
            f'echo "$s" | grep "is pending: Pulling: Pulling image"'
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_start_stop_two_nodes():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure-start-stop-two-nodes',
        [
            f'sky launch --num-nodes=2 -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/azure_start_stop.yaml',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky stop -y {name}',
            f'sky start -y {name} -i 1',
            f'sky exec --num-nodes=2 {name} examples/azure_start_stop.yaml',
            f'sky logs {name} 2 --status',  # Ensure the job succeeded.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[
                    sky.ClusterStatus.INIT, sky.ClusterStatus.STOPPED
                ],
                timeout=235) +
            f'|| {{ ssh {name} "cat ~/.sky/skylet.log"; exit 1; }}'
        ],
        f'sky down -y {name}',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing env for disk tier ----------
@pytest.mark.aws
@pytest.mark.no_seeweb  # Seeweb does not support custom disk tiers
def test_aws_disk_tier():

    def _get_aws_query_command(region: str, instance_id: str, field: str,
                               expected: str):
        return (f'aws ec2 describe-volumes --region {region} '
                f'--filters Name=attachment.instance-id,Values={instance_id} '
                f'--query Volumes[*].{field} | grep {expected} ; ')

    cluster_name = smoke_tests_utils.get_cluster_name()
    for disk_tier in list(resources_utils.DiskTier):
        specs = AWS._get_disk_specs(disk_tier)
        name = cluster_name + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.AWS.max_cluster_name_length())
        region = 'us-east-2'
        test = smoke_tests_utils.Test(
            'aws-disk-tier-' + disk_tier.value,
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
                f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    cmd=
                    (f'id=`aws ec2 describe-instances --region {region} --filters '
                     f'Name=tag:ray-cluster-name,Values={name_on_cloud} --query '
                     f'Reservations[].Instances[].InstanceId --output text`; ' +
                     _get_aws_query_command(region, '$id', 'VolumeType',
                                            specs['disk_tier']) +
                     ('' if specs['disk_tier']
                      == 'standard' else _get_aws_query_command(
                          region, '$id', 'Iops', specs['disk_iops'])) +
                     ('' if specs['disk_tier'] != 'gp3' else
                      _get_aws_query_command(region, '$id', 'Throughput',
                                             specs['disk_throughput'])))),
            ],
            f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
            timeout=10 * 60,  # 10 mins  (it takes around ~6 mins)
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.parametrize('instance_types',
                         [['n2-standard-2', 'n2-standard-64']])
def test_gcp_disk_tier(instance_types: List[str]):
    instance_type_low, instance_type_high = instance_types
    for disk_tier in list(resources_utils.DiskTier):
        # GCP._get_disk_type returns pd-extreme only for instance types with >= 64
        # CPUs. We must ensure the launched instance type matches what we pass to
        # GCP._get_disk_type.
        if disk_tier == resources_utils.DiskTier.BEST:
            instance_type = instance_type_high
        else:
            instance_type = instance_type_low

        disk_types = [GCP._get_disk_type(instance_type, disk_tier)]
        name = smoke_tests_utils.get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.GCP.max_cluster_name_length())
        region = 'us-central1'
        instance_type_options = [f'--instance-type {instance_type}']
        if disk_tier == resources_utils.DiskTier.BEST:
            # Ultra disk tier requires n2 instance types to have more than 64 CPUs.
            # If using default instance type, it will only enable the high disk tier.
            # Test both scenarios: n2-standard-2 maps to HIGH, n2-standard-64 maps to ULTRA
            disk_types = [
                GCP._get_disk_type(instance_type_low,
                                   resources_utils.DiskTier.HIGH),
                GCP._get_disk_type(instance_type,
                                   resources_utils.DiskTier.ULTRA),
            ]
            instance_type_options = [
                f'--instance-type {instance_type_low}',
                f'--instance-type {instance_type}'
            ]
        for disk_type, instance_type_option in zip(disk_types,
                                                   instance_type_options):
            test = smoke_tests_utils.Test(
                'gcp-disk-tier-' + disk_tier.value,
                [
                    smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
                    f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                    f'--disk-tier {disk_tier.value} {instance_type_option} ',
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=(f'name=`gcloud compute instances list --filter='
                             f'"labels.ray-cluster-name:{name_on_cloud}" '
                             '--format="value(name)"`; '
                             f'gcloud compute disks list --filter="name=$name" '
                             f'--format="value(type)" | grep {disk_type}'))
                ],
                f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
                timeout=6 * 60,  # 6 mins  (it takes around ~3 mins)
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_disk_tier():
    for disk_tier in list(resources_utils.DiskTier):
        if disk_tier == resources_utils.DiskTier.HIGH or disk_tier == resources_utils.DiskTier.ULTRA:
            # Azure does not support high and ultra disk tier.
            continue
        type = Azure._get_disk_type(disk_tier)
        name = smoke_tests_utils.get_cluster_name() + '-' + disk_tier.value
        name_on_cloud = common_utils.make_cluster_name_on_cloud(
            name, sky.Azure.max_cluster_name_length())
        region = 'eastus2'
        test = smoke_tests_utils.Test(
            'azure-disk-tier-' + disk_tier.value,
            [
                f'sky launch -y -c {name} --infra azure/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--disk-tier {disk_tier.value} echo "hello sky"',
                f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
                f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
                f'--output tsv | grep {type}'
            ],
            f'sky down -y {name}',
            timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_best_tier_failover():
    type = Azure._get_disk_type(resources_utils.DiskTier.LOW)
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Azure.max_cluster_name_length())
    region = 'eastus2'
    test = smoke_tests_utils.Test(
        'azure-best-tier-failover',
        [
            f'sky launch -y -c {name} --infra azure/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'--disk-tier best --instance-type Standard_D8_v5 echo "hello sky"',
            f'az resource list --tag ray-cluster-name={name_on_cloud} --query '
            f'"[?type==\'Microsoft.Compute/disks\'].sku.name" '
            f'--output tsv | grep {type}',
        ],
        f'sky down -y {name}',
        timeout=20 * 60,  # 20 mins  (it takes around ~12 mins)
    )
    smoke_tests_utils.run_one_test(test)


# ------ Testing Zero Quota Failover ------
@pytest.mark.aws
def test_aws_zero_quota_failover():

    name = smoke_tests_utils.get_cluster_name()
    region = smoke_tests_utils.get_aws_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for EC2 P3 instances were found on all AWS regions. Is this '
            'expected for your account?')
        return

    test = smoke_tests_utils.Test(
        'aws-zero-quota-failover',
        [
            f'sky launch -y -c {name} --infra aws/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus V100:8 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_zero_quota_failover():

    name = smoke_tests_utils.get_cluster_name()
    region = smoke_tests_utils.get_gcp_region_for_quota_failover()

    if not region:
        pytest.xfail(
            'Unable to test zero quota failover optimization — quotas '
            'for A100-80GB GPUs were found on all GCP regions. Is this '
            'expected for your account?')
        return

    test = smoke_tests_utils.Test(
        'gcp-zero-quota-failover',
        [
            f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus A100-80GB:1 --use-spot | grep "Found no quota"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_hyperbolic  # Hyperbolic doesn't support host controller and auto-stop
def test_long_setup_run_script(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_',
                                     suffix='.yaml') as f:
        debug_command = (
            f'sky exec {name} "source ~/skypilot-runtime/bin/activate; '
            f'ray status --verbose; ray list tasks --detail; '
            f'find /home/sky/sky_logs -name "*.log" -type f -exec tail -f '
            f'{{}} +"')
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

        test = smoke_tests_utils.Test(
            'long-setup-run-script',
            [
                f'sky launch -y -c {name} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {f.name}',
                f'sky exec {name} "echo hello"',
                f'sky exec {name} {f.name}',
                f'sky logs {name} --status 1',
                f'sky logs {name} --status 2',
                f'sky logs {name} --status 3',
                f'sky down {name} -y',
                f'sky jobs launch -y -n {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} {f.name}',
                f'sky jobs queue | grep {name} | grep SUCCEEDED',
            ],
            debug_command +
            f'; sky down -y {name}; sky jobs cancel -n {name} -y',
        )
        smoke_tests_utils.run_one_test(test)


# ---------- Test GCP network tier ----------
@pytest.mark.gcp
def test_gcp_network_tier():
    """Test GCP network tier functionality for standard tier."""
    network_tier = resources_utils.NetworkTier.STANDARD
    # Use n2-standard-4 instance type for testing
    instance_type = 'n2-standard-4'
    name = smoke_tests_utils.get_cluster_name() + '-' + network_tier.value
    region = 'us-central1'

    # For standard tier, verify basic network functionality
    verification_commands = [
        smoke_tests_utils.run_cloud_cmd_on_cluster(
            name, cmd='echo "Standard network tier verification"')
    ]

    test_commands = [
        smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
        f'sky launch -y -c {name} --infra gcp/{region} {smoke_tests_utils.LOW_RESOURCE_ARG} '
        f'--network-tier {network_tier.value} --instance-type {instance_type} '
        f'echo "Testing network tier {network_tier.value}"',
    ] + verification_commands

    test = smoke_tests_utils.Test(
        f'gcp-network-tier-{network_tier.value}',
        test_commands,
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=10 * 60,  # 10 mins
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_network_tier_with_gpu():
    """Test GCP network_tier=best with GPU to verify GPU Direct functionality."""
    name = smoke_tests_utils.get_cluster_name() + '-gpu-best'
    cmd = 'echo "LD_LIBRARY_PATH check for GPU workloads:" && echo $LD_LIBRARY_PATH && echo $LD_LIBRARY_PATH | grep -q "/usr/local/nvidia/lib64:/usr/local/tcpx/lib64" && echo "LD_LIBRARY_PATH contains required paths" || exit 1'
    test = smoke_tests_utils.Test(
        'gcp-network-tier-best-gpu',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky launch -y -c {name} --cloud gcp '
            f'--gpus H100:8 --network-tier best '
            f'echo "Testing network tier best with GPU"',
            # Check if LD_LIBRARY_PATH contains the required NCCL and TCPX paths for GPU workloads
            f'sky exec {name} {shlex.quote(cmd)} && sky logs {name} --status'
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=25 * 60,  # 25 mins for GPU provisioning
    )
    smoke_tests_utils.run_one_test(test)


def test_remote_server_api_login():
    if not smoke_tests_utils.is_remote_server_test():
        pytest.skip('This test is only for remote server')

    endpoint = smoke_tests_utils.get_api_server_url()
    config_path = skypilot_config._GLOBAL_CONFIG_PATH
    backup_path = f'{config_path}.backup_for_test_remote_server_api_login'

    test = smoke_tests_utils.Test(
        'remote-server-api-login',
        [
            # Backup existing config file if it exists
            f'if [ -f {config_path} ]; then cp {config_path} {backup_path}; fi',
            # Run sky api login
            f'unset {constants.SKY_API_SERVER_URL_ENV_VAR} && sky api login -e {endpoint}',
            # Echo the config file content to see what was written
            f'echo "Config file content after sky api login:" && cat {config_path}',
            # Verify the config file is updated with the endpoint
            f'grep -q "endpoint: {endpoint}" {config_path}',
            # Verify the api_server section exists
            f'grep -q "api_server:" {config_path}',
        ],
        # Restore original config file if backup exists
        f'if [ -f {backup_path} ]; then mv {backup_path} {config_path}; fi',
    )

    with pytest.MonkeyPatch().context() as m:
        m.setattr(docker_utils, 'get_api_server_endpoint_inside_docker',
                  lambda: 'http://255.255.255.255:41540')
        # Mock the environment config to return a non-existing endpoint.
        # The sky api login command should not read from environment config
        # when an explicit endpoint is provided as an argument.
        smoke_tests_utils.run_one_test(test, check_sky_status=False)


# ---------- Testing Autostopping ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_nebius  # Nebius does not support autodown
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_kubernetes  # Kubernetes does not autostop yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support autostop
@pytest.mark.no_slurm  # Slurm does not support autostop yet
def test_autostop_with_unhealthy_ray_cluster(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # See test_autostop_wait_for_jobs() for explanation of autostop_timeout.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    test = smoke_tests_utils.Test(
        'autostop_with_unhealthy_ray_cluster',
        [
            f'sky launch -y -d -c {name} --num-nodes 2 --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} -i 5',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "5m"',
            # Ensure the job succeeded.
            f'sky logs {name} 1 --status',
            # Stop the ray cluster, but leave the node running.
            # TODO(kevin): Find a better way to replicate the issue
            f'ssh {name} "skypilot-runtime/bin/ray stop"',
            # Ensure the cluster is not terminated early, and is in INIT,
            # because the ray cluster is stopped.
            'sleep 240',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep INIT',
            # Ensure the cluster is STOPPED.
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Autodowning ----------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping in SkyPilot implementation
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
@pytest.mark.no_nebius  # Nebius does not support autodown
@pytest.mark.no_hyperbolic  # Hyperbolic does not support num_nodes > 1 yet
@pytest.mark.no_shadeform  # Shadeform does not support num_nodes > 1 yet
@pytest.mark.no_seeweb  # Seeweb does not support autostop
def test_autodown(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    num_nodes = 2
    # Azure takes ~ 13m30s (810s) to autodown a VM, so here we use 900 to ensure
    # the VM is terminated.
    if generic_cloud == 'azure':
        autodown_timeout = 900
        total_timeout_minutes = 90
    elif generic_cloud == 'kubernetes':
        autodown_timeout = 300
        total_timeout_minutes = 30
    else:
        autodown_timeout = 240
        total_timeout_minutes = 20
    check_autostop_set = f's=$(sky status) && echo "$s" && echo "==check autostop set==" && echo "$s" | grep {name} | grep "1m (down)"'
    test = smoke_tests_utils.Test(
        'autodown',
        [
            f'sky launch -y -d -c {name} --num-nodes {num_nodes} --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            check_autostop_set,
            # Ensure the cluster is not terminated early.
            'sleep 40',
            f's=$(sky status {name} --refresh); echo "$s"; echo; echo; echo "$s"  | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            f'sleep {autodown_timeout}',
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|Cluster \'{name}\' not found"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            # Fails here with: E 11-25 21:23:59 sdk.py:389] RuntimeError: Failed to run setup commands on an instance. (exit code 1). Error: ===== stdout =====
            # E 11-25 21:23:59 sdk.py:389] srun: error: Slurm job 665 has expired
            f'sky launch -y -d -c {name} --infra {generic_cloud} --num-nodes {num_nodes} --down {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} --infra {generic_cloud} tests/test_yamls/minimal.yaml',
            check_autostop_set,
            f'sleep {autodown_timeout}',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|Cluster \'{name}\' not found"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} --infra {generic_cloud} --num-nodes {num_nodes} --down {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            f'sleep {autodown_timeout}',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status {name} --refresh) && echo "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_autodown():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'SCP_autodown',
        [
            f'sky launch -y -d -c {name} {smoke_tests_utils.SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --down -i 1',
            # Ensure autostop is set.
            f'sky status | grep {name} | grep "1m (down)"',
            # Ensure the cluster is not terminated early.
            'sleep 45',
            f'sky status --refresh | grep {name} | grep UP',
            # Ensure the cluster is terminated.
            'sleep 200',
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {smoke_tests_utils.SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep UP',  # Ensure the cluster is UP.
            f'sky exec {name} {smoke_tests_utils.SCP_TYPE} tests/test_yamls/minimal.yaml',
            f'sky status | grep {name} | grep "1m (down)"',
            'sleep 200',
            # Ensure the cluster is terminated.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && {{ echo "$s" | grep {name} | grep "Autodowned cluster\|terminated on the cloud"; }} || {{ echo "$s" | grep {name} && exit 1 || exit 0; }}',
            f'sky launch -y -d -c {name} {smoke_tests_utils.SCP_TYPE} --down tests/test_yamls/minimal.yaml',
            f'sky autostop -y {name} --cancel',
            'sleep 200',
            # Ensure the cluster is still UP.
            f's=$(SKYPILOT_DEBUG=0 sky status --refresh) && printf "$s" && echo "$s" | grep {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=25 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def _get_k8s_service_cleanup_check_cmd(name: str, name_on_cloud: str) -> str:
    """Returns the command to check that Kubernetes services are cleaned up."""
    return smoke_tests_utils.run_cloud_cmd_on_cluster(
        name,
        f'services=$(kubectl get svc -l skypilot-cluster-name={name_on_cloud} -o name || true); '
        'echo "Services: [$services]"; '
        'if [ -n "$services" ]; then echo "ERROR: services still exist"; exit 1; '
        'else echo "OK: services cleaned up"; fi')


# ---------- Testing Recovery on Kubernetes ----------
@pytest.mark.kubernetes
def test_kubernetes_recovery():
    """Test Kubernetes recovery."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())
    head = f'{name_on_cloud}-head'
    worker2 = f'{name_on_cloud}-worker2'
    worker3 = f'{name_on_cloud}-worker3'
    service_cleanup_check = _get_k8s_service_cleanup_check_cmd(
        name, name_on_cloud)
    test = smoke_tests_utils.Test(
        'kubernetes_pod_recovery',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 4 \'set -e;ps aux | grep -v "grep " | grep "ray/raylet/raylet"\'',
            f'sky logs {name} --status 1',

            # Check launching again
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 4 \'set -e;ps aux | grep -v "grep " | grep "ray/raylet/raylet"\'',
            f'sky logs {name} --status 2',

            # Delete head, worker-2 and worker-3
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'kubectl get pod -l ray-cluster-name={name_on_cloud} && kubectl delete pod {head} {worker2} {worker3}'
            ),
            # Check launching again
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 4 \'set -e;ps aux | grep -v "grep " | grep "ray/raylet/raylet"\'',
            f'sky logs {name} --status 1',

            # Check launching again
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 4 \'set -e;ps aux | grep -v "grep " | grep "ray/raylet/raylet"\'',
            f'sky logs {name} --status 2',

            # Delete all Pods
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'kubectl get pod -l ray-cluster-name={name_on_cloud} && kubectl delete pod -l ray-cluster-name={name_on_cloud}'
            ),
            # Check status
            f'sky status -r {name} --no-show-pools --no-show-services --no-show-managed-jobs',
        ],
        f'sky down -y {name} && {service_cleanup_check} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_service_cleanup_on_down():
    """Test that Kubernetes services are cleaned up when running sky down
    after pods have been externally deleted."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())
    service_cleanup_check = _get_k8s_service_cleanup_check_cmd(
        name, name_on_cloud)
    test = smoke_tests_utils.Test(
        'kubernetes_service_cleanup_on_down',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch cluster
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 2 echo hello',
            # Verify services exist
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'services=$(kubectl get svc -l skypilot-cluster-name={name_on_cloud} -o name); '
                'echo "Services before deletion: [$services]"; '
                'if [ -z "$services" ]; then echo "ERROR: no services found"; exit 1; fi'
            ),
            # Delete all pods externally (simulating external deletion)
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'kubectl delete pod -l ray-cluster-name={name_on_cloud} --wait'
            ),
            # Run sky down - this should clean up services even though pods are gone
            f'sky down -y {name}',
            # Verify services are cleaned up
            service_cleanup_check,
        ],
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_service_cleanup_on_status_refresh():
    """Test that Kubernetes services are cleaned up by the status refresh
    daemon after pods have been externally deleted."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())
    service_cleanup_check = _get_k8s_service_cleanup_check_cmd(
        name, name_on_cloud)
    test = smoke_tests_utils.Test(
        'kubernetes_service_cleanup_on_status_refresh',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            # Launch cluster
            f'sky launch -y -c {name} --infra kubernetes --cpus 0.1+ --num-nodes 2 echo hello',
            # Verify services exist
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'services=$(kubectl get svc -l skypilot-cluster-name={name_on_cloud} -o name); '
                'echo "Services before deletion: [$services]"; '
                'if [ -z "$services" ]; then echo "ERROR: no services found"; exit 1; fi'
            ),
            # Delete all pods externally (simulating external deletion)
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'kubectl delete pod -l ray-cluster-name={name_on_cloud} --wait'
            ),
            # Wait for status refresh to detect termination and clean up
            # The status refresh daemon runs periodically and should detect
            # that all pods are gone, triggering post_teardown_cleanup
            f'sleep 90',
            # Verify cluster is removed from sky status
            f'sky status {name} --no-show-pools --no-show-services --no-show-managed-jobs 2>&1 | grep -q "not found"',
            # Verify services are cleaned up
            service_cleanup_check,
        ],
        f'sky down -y {name} || true; {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Kubernetes pod_config ----------
@pytest.mark.kubernetes
def test_kubernetes_pod_config_pvc():
    """Test Kubernetes pod_config with PVC volume mounts."""
    name = smoke_tests_utils.get_cluster_name()
    pvc_name = f'{name}-pvc'

    template_str = pathlib.Path(
        'tests/test_yamls/test_k8s_pod_config_pvc.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    task_yaml_content = template.render(pvc_name=pvc_name)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as f:
        f.write(task_yaml_content)
        f.flush()
        task_yaml_path = f.name

        test = smoke_tests_utils.Test(
            'kubernetes_pod_config_pvc',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, f'kubectl create -f - <<EOF\n'
                    f'apiVersion: v1\n'
                    f'kind: PersistentVolumeClaim\n'
                    f'metadata:\n'
                    f'  name: {pvc_name}\n'
                    f'spec:\n'
                    f'  accessModes:\n'
                    f'    - ReadWriteOnce\n'
                    f'  resources:\n'
                    f'    requests:\n'
                    f'      storage: 1Gi\n'
                    f'EOF'),
                # Verify PVC was created
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name, f'kubectl get pvc {pvc_name} -oyaml'),
                f'sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_path}',
                # Write to the volume
                f'sky exec {name} --infra kubernetes "ls -la /mnt/test-data/ && echo \'Hello\' > /mnt/test-data/hello.txt"',
                # Down and launch again
                f'sky down -y {name}',
                f'sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_path}',
                # Read the volume from the new pod
                f'sky exec {name} --infra kubernetes "ls -la /mnt/test-data/ && cat /mnt/test-data/hello.txt"',
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'kubectl delete pvc {pvc_name} --ignore-not-found=true --wait=false || true'
                ),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)
        os.unlink(task_yaml_path)


# ---------- Testing Launching with Pending Pods on Kubernetes ----------
@pytest.mark.kubernetes
def test_launching_with_pending_pods():
    """Test Kubernetes launching with pending pods."""
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())
    head = f'{name_on_cloud}-head'
    test = smoke_tests_utils.Test(
        'kubernetes_pod_pending',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('kubernetes', name),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, f'kubectl create -f - <<EOF\n'
                f'apiVersion: v1\n'
                f'kind: Pod\n'
                f'metadata:\n'
                f'  name: {head}\n'
                f'  labels:\n'
                f'    parent: skypilot\n'
                f'    ray-node-type: head\n'
                f'    skypilot-head-node: "1"\n'
                f'    ray-cluster-name: {name_on_cloud}\n'
                f'    skypilot-cluster-name: {name_on_cloud}\n'
                f'spec:\n'
                f'  containers:\n'
                f'  - command:\n'
                f'    - /bin/sh\n'
                f'    - -c\n'
                f'    - sleep 365d\n'
                f'    image: us-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot:latest\n'
                f'    imagePullPolicy: IfNotPresent\n'
                f'    name: skypilot\n'
                f'  nodeSelector:\n'
                f'    test: test\n'
                f'EOF'),
            # Check Pod pending
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name, f'kubectl get pod {head} | grep "Pending"'),
            f's=$(SKYPILOT_DEBUG=1 sky launch -y -c {name} --infra kubernetes --cpus 0.1+ \'echo hi\'); echo "$s"; echo; echo; echo "$s" | grep "Timed out while waiting for nodes to start"',
            # Check Pods have been deleted
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                f'pod=$(kubectl get pod -l ray-cluster-name={name_on_cloud} | grep {head}); if [ -n "$pod" ]; then exit 1; fi'
            ),
        ],
        f'sky down -y {name} && '
        f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_pod_config_change_detection():
    """Test that pod_config changes are detected and warning is shown."""
    name = smoke_tests_utils.get_cluster_name()

    template_str_1 = pathlib.Path(
        'tests/test_yamls/test_k8s_pod_config_env1.yaml.j2').read_text()
    template_1 = jinja2.Template(template_str_1)
    task_yaml_1_content = template_1.render()

    template_str_2 = pathlib.Path(
        'tests/test_yamls/test_k8s_pod_config_env2.yaml.j2').read_text()
    template_2 = jinja2.Template(template_str_2)
    task_yaml_2_content = template_2.render()

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w', delete=False) as f1, \
         tempfile.NamedTemporaryFile(suffix='.yaml', mode='w', delete=False) as f2:

        f1.write(task_yaml_1_content)
        f1.flush()
        task_yaml_1_path = f1.name

        f2.write(task_yaml_2_content)
        f2.flush()
        task_yaml_2_path = f2.name

        test = smoke_tests_utils.Test(
            'kubernetes_pod_config_change_detection',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                # Launch task with original pod_config
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_1_path}); echo "$s"; echo; echo; echo "$s" | grep "TEST_VAR_1 = 1"',
                # Launch task with modified pod_config - should show warning
                # Verify the job succeeds despite the warning and check that environment variables are not updated
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_2_path} 2>&1); echo "$s"; echo; echo; echo "$s" | grep "Task requires different pod config" && '
                f'echo "$s" | grep "TEST_VAR_1 = 1" && '
                f'echo "$s" | grep "TEST_VAR_2 ="',
                # Down and launch again to get the new pod_config
                f'sky down -y {name}',
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_2_path}); echo "$s"; echo; echo; echo "$s" | grep "TEST_VAR_1 = 2" && '
                f'echo "$s" | grep "TEST_VAR_2 = 2"',
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)
        os.unlink(task_yaml_1_path)
        os.unlink(task_yaml_2_path)


# ---------- Testing Kubernetes remote_identity override ----------
@pytest.mark.kubernetes
def test_kubernetes_remote_identity_override():
    """Test that config.kubernetes.remote_identity can be overridden in task YAML.

    This test verifies that:
    1. With remote_identity: LOCAL_CREDENTIALS, kubeconfig is uploaded
    2. With remote_identity: NO_UPLOAD, kubeconfig is NOT uploaded

    This is important for users running SkyPilot from within a SkyPilot pod,
    where the auto-mounted service account should be used instead of uploading
    kubeconfig which may have exec auth or unreachable IPs.

    Fixes: https://github.com/skypilot-org/skypilot/issues/8321
    """
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'kubernetes_remote_identity_override',
        [
            # First, launch with LOCAL_CREDENTIALS - kubeconfig should be uploaded
            f'sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/test_k8s_remote_identity_local_creds.yaml',
            f'sky logs {name} 1 --status',
            # Down the cluster
            f'sky down -y {name}',
            # Launch with NO_UPLOAD - kubeconfig should NOT be uploaded
            f'sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/test_k8s_remote_identity_no_upload.yaml',
            f'sky logs {name} 1 --status',
        ],
        f'sky down -y {name}',
        timeout=15 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_pod_config_sidecar():
    """Test Kubernetes pod_config with sidecar container injection.

    This test verifies that SkyPilot correctly handles pods with multiple
    containers (sidecars) by:
    1. Launching a cluster with a sidecar container via pod_config
    2. Verifying the pod has both ray-node and sidecar containers
    3. Verifying sky exec commands run in the ray-node container
    4. Verifying the sidecar container is running
    """
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())

    template_str = pathlib.Path(
        'tests/test_yamls/test_k8s_pod_config_sidecar.yaml.j2').read_text()
    template = jinja2.Template(template_str)
    task_yaml_content = template.render()

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as f:
        f.write(task_yaml_content)
        f.flush()
        task_yaml_path = f.name

        test = smoke_tests_utils.Test(
            'kubernetes_pod_config_sidecar',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                # Launch SkyPilot cluster with sidecar
                f'sky launch -y -c {name} --infra kubernetes '
                f'{smoke_tests_utils.LOW_RESOURCE_ARG} {task_yaml_path}',
                # Verify pod has 2 containers (ray-node and sidecar)
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'kubectl get pod -l skypilot-cluster-name={name_on_cloud} '
                    '-o jsonpath=\'{.items[0].spec.containers[*].name}\' | '
                    'grep -E "ray-node.*sidecar|sidecar.*ray-node"'),
                # Verify sky exec runs in ray-node container
                f'sky exec {name} "echo CONTAINER_CHECK: ray-node is working"',
                # Verify sidecar is running
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'kubectl logs -l skypilot-cluster-name={name_on_cloud} '
                    '-c sidecar --tail=5 | grep "sidecar running"'),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)
        os.unlink(task_yaml_path)


# ---------- Testing Kubernetes set_pod_resource_limits ----------
@pytest.mark.kubernetes
def test_kubernetes_set_pod_resource_limits():
    """Test that set_pod_resource_limits config sets CPU/memory limits on pods.

    This test verifies that when kubernetes.set_pod_resource_limits is set to
    a numeric multiplier, the pod limits are set to requests * multiplier.
    """
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.Kubernetes.max_cluster_name_length())

    # Config with set_pod_resource_limits with a 2x multiplier
    config = textwrap.dedent("""
    kubernetes:
        set_pod_resource_limits: 2.0
    """)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as config_file:
        config_file.write(config)
        config_file.flush()
        config_path = config_file.name

        test = smoke_tests_utils.Test(
            'kubernetes_set_pod_resource_limits',
            [
                smoke_tests_utils.launch_cluster_for_cloud_cmd(
                    'kubernetes', name),
                # Launch a cluster with set_pod_resource_limits=2.0
                # Using --cpus 2 --memory 2 so limits should be 4 CPU, 4G memory
                f'sky launch -y -c {name} --infra kubernetes --cpus 2 --memory 2',
                # Verify CPU limit is set (should be 4 with 2x multiplier)
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'kubectl get pod -l ray-cluster-name={name_on_cloud} '
                    '-o jsonpath=\'{.items[0].spec.containers[0].resources.limits.cpu}\' '
                    '| grep -E "^4"'),
                # Verify memory limit is set (should be 4G with 2x multiplier)
                smoke_tests_utils.run_cloud_cmd_on_cluster(
                    name,
                    f'kubectl get pod -l ray-cluster-name={name_on_cloud} '
                    '-o jsonpath=\'{.items[0].spec.containers[0].resources.limits.memory}\' '
                    '| grep -E "^4.*G"'),
            ],
            f'sky down -y {name} && '
            f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name)} && '
            f'rm -f {config_path}',
            timeout=10 * 60,
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: config_path,
            },
        )
        smoke_tests_utils.run_one_test(test)


# ---------- SSH Proxy Performance Test ----------
@pytest.mark.kubernetes
@pytest.mark.no_remote_server
def test_kubernetes_ssh_proxy_performance():
    """Test Kubernetes SSH proxy performance with high load.

    This test launches a Kubernetes cluster and runs the SSH proxy benchmark
    to ensure that SSH latency remains low (< 0.01s) under high load conditions.
    """
    cluster_name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'kubernetes_ssh_proxy_performance',
        [
            # Launch a minimal Kubernetes cluster for SSH proxy testing
            f'sky launch -y -c {cluster_name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} echo "SSH proxy test cluster ready"',
            # Run the SSH proxy benchmark test and validate results using pipes
            f'python tests/load_tests/test_ssh_proxy.py -c {cluster_name} -p 20 -n 100 --size 1024 2>&1 | tee /dev/stderr | ( '
            f'OUTPUT=$(cat) && '
            f'echo "$OUTPUT" && '
            f'echo "Validating performance metrics..." && '
            f'MEAN=$(echo "$OUTPUT" | grep "Mean:" | awk \'{{print $2}}\' | sed \'s/s$//\') && '
            f'MEDIAN=$(echo "$OUTPUT" | grep "Median:" | awk \'{{print $2}}\' | sed \'s/s$//\') && '
            f'STDDEV=$(echo "$OUTPUT" | grep "Std Dev:" | awk \'{{print $3}}\' | sed \'s/s$//\') && '
            f'SUCCESS=$(echo "$OUTPUT" | grep "Success rate:" | awk \'{{print $3}}\' | sed \'s/%$//\') && '
            f'echo "Mean: $MEAN, Median: $MEDIAN, Std Dev: $STDDEV, Success: $SUCCESS%" && '
            f'if [ "$(echo "$MEAN < 0.01" | bc -l)" -eq 1 ]; then echo "Mean latency OK: ${{MEAN}}s"; else echo "Mean latency too high: ${{MEAN}}s"; exit 1; fi && '
            f'if [ "$(echo "$MEDIAN < 0.01" | bc -l)" -eq 1 ]; then echo "Median latency OK: ${{MEDIAN}}s"; else echo "Median latency too high: ${{MEDIAN}}s"; exit 1; fi && '
            f'if [ "$(echo "$STDDEV < 0.02" | bc -l)" -eq 1 ]; then echo "Std Dev OK: ${{STDDEV}}s"; else echo "Std Dev too high: ${{STDDEV}}s"; exit 1; fi && '
            f'if [ "$SUCCESS" = "100.00" ] || [ "$SUCCESS" = "100" ]; then echo "Success rate OK: ${{SUCCESS}}%"; else echo "Success rate too low: ${{SUCCESS}}%"; exit 1; fi '
            f')',
        ],
        f'sky down -y {cluster_name}',
        timeout=15 * 60,  # 15 minutes timeout
    )
    smoke_tests_utils.run_one_test(test)


def test_cancel_logs_does_not_break_process_pool(generic_cloud: str):
    """Test that canceling sky logs doesn't break the process pool.

    Regression test for cascading BrokenProcessPool errors
    when coroutine requests (like sky logs) are cancelled.
    """
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cancel_logs_does_not_break_process_pool',
        [
            # Launch cluster 1 with long-running job, in detached mode.
            f'sky launch -c {name}-1 -y -d --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} \'for i in {{1..180}}; do echo $i; sleep 1; done\'',
            # Start sky logs in background, launch cluster 2 in background,
            # send SIGTERM to sky logs, then wait for launch to finish.
            f'sky logs {name}-1 & '
            f'LOGS_PID=$!; '
            f'sky launch -c {name}-2 -y --infra {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} echo hi > /tmp/{name}-2.log 2>&1 & '
            f'LAUNCH_PID=$!; '
            'sleep 10; '
            f'echo "Killing logs PID: $LOGS_PID"; '
            f'kill -9 $LOGS_PID; '
            f'echo "Waiting for launch PID: $LAUNCH_PID"; '
            f'tail -f /tmp/{name}-2.log & '
            f'wait $LAUNCH_PID',
            # Verify launch succeeded
            f'cat /tmp/{name}-2.log | grep sky-cmd | grep hi',
            f'! cat /tmp/{name}-2.log | grep BrokenProcessPool',
        ],
        f'sky down -y {name}-1; sky down -y {name}-2; rm -f /tmp/{name}-*.log',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)
