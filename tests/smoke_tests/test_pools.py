import os
import tempfile
import textwrap
from typing import Dict, List, Optional

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import yaml_utils

# 1. TODO(lloyd): Marking below tests as no_remote_server since PR#7332 changed
# the resource management logic for pools reducing the number of concurrent
# pools that can be running. This leads to build failures on the shared GKE
# test cluster. Remove this when consolidation mode is enabled by default or
# we have an option to not allow shared env tests.

_LAUNCH_POOL_AND_CHECK_SUCCESS = (
    's=$(sky jobs pool apply -p {pool_name} {pool_yaml} -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Successfully created pool"')

_LAUNCH_JOB_AND_CHECK_SUCCESS = (
    's=$(sky jobs launch --pool {pool_name} {job_yaml} -d -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Job submitted"; '
    'sleep 5')

_LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME = (
    's=$(sky jobs launch --pool {pool_name} {job_yaml} -n {job_name} -d -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Job submitted"; '
    'sleep 5')

_LAUNCH_JOB_AND_CHECK_OUTPUT = (
    's=$(sky jobs launch --pool {pool_name} {job_yaml} -d -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Job submitted"; echo "$s" | grep "{output}"; '
    'sleep 5')

_POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS = (
    's=$(sky jobs pool apply -p {pool_name} --workers {num_workers} -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Successfully updated pool"')

_TERMINATE_INSTANCE = (
    'id={get_instance_id_cmd} && '
    'echo "Instance ID: $id" && '
    # Make sure the instance id is not empty.
    'if [[ -z "$id" ]]; then '
    '  echo "Instance ID is empty" && exit 1; '
    ' fi && '
    'aws ec2 terminate-instances --region {region} '
    '--instance-ids $id && '
    'echo "Instance terminate command ran" && '
    # Wait for the instance to be stopped before restarting.
    'aws ec2 wait instance-terminated --region {region} '
    '--instance-ids $id && '
    'echo "Instance terminated"')

_TEARDOWN_POOL = ('sky jobs pool down {pool_name} -y')

_CANCEL_POOL_JOBS = ('sky jobs cancel --pool {pool_name} -y')


def cancel_job(job_name: str):
    return f'sky jobs cancel -n {job_name} -y'


def cancel_jobs_and_teardown_pool(pool_name: str, timeout: int = 3):
    return f'{_CANCEL_POOL_JOBS.format(pool_name=pool_name)} || true && ' \
           f'sleep {timeout} && ' \
           f'{_TEARDOWN_POOL.format(pool_name=pool_name)}'


def wait_until_pool_ready(pool_name: str,
                          timeout: int = 30,
                          time_between_checks: int = 5):
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for job to succeed"; exit 1; '
        'fi; '
        f's=$(sky jobs pool status {pool_name}); '
        'echo "$s"; '
        'if echo "$s" | grep "FAILED"; then '
        '  exit 1; '
        'fi; '
        'if echo "$s" | grep "SHUTTING_DOWN"; then '
        '  exit 1; '
        'fi; '
        'if echo "$s" | grep "READY"; then '
        '  break; '
        'fi; '
        'echo "Waiting for pool to be ready..."; '
        f'sleep {time_between_checks}; '
        'done')


def wait_until_worker_status(pool_name: str,
                             status: str,
                             timeout: int = 30,
                             time_between_checks: int = 5,
                             num_occurrences: int = 1):
    status_str = f'sky jobs pool status {pool_name} | grep -A999 "^Pool Workers" | grep "^{pool_name}"'
    count_check = (
        f'count=$(echo "$s" | grep -c "{status}" || echo "0"); '
        f'if (( count != {num_occurrences} )); then '
        f'  echo "Expected {num_occurrences} occurrences of status \'{status}\', but found $count"; '
        '  continue; '
        'fi; ')
    waiting_msg_suffix = f' with {num_occurrences} occurrences'
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for worker status \'{status}\'{waiting_msg_suffix}"; exit 1; '
        'fi; '
        f's=$({status_str}); '
        'echo "$s"; '
        f'{count_check}'
        f'if echo "$s" | grep "{status}"; then '
        '  break; '
        'fi; '
        'if echo "$s" | grep "FAILED"; then '
        '  exit 1; '
        'fi; '
        'if echo "$s" | grep "SHUTTING_DOWN"; then '
        '  exit 1; '
        'fi; '
        f'echo "Waiting for worker status to be {status}{waiting_msg_suffix}..."; '
        f'sleep {time_between_checks}; '
        'done; '
        'sleep 1')


def wait_until_job_status(
        job_name: str,
        good_statuses: List[str],
        bad_statuses: List[str] = ['CANCELLED', 'FAILED_CONTROLLER'],
        timeout: int = 30,
        time_between_checks: int = 5):
    s = 'start_time=$SECONDS; '
    s += 'while true; do '
    s += f'if (( $SECONDS - $start_time > {timeout} )); then '
    s += f'  echo "Timeout after {timeout} seconds waiting for job to succeed"; exit 1; '
    s += 'fi; '
    s += 's=$(sky jobs queue); '
    s += 'echo "$s"; '
    for status in good_statuses:
        s += f'if echo "$s" | grep "{job_name}" | grep "{status}"; then '
        s += '  break; '
        s += 'fi; '
    for status in bad_statuses:
        s += f'if echo "$s" | grep "{job_name}" | grep "{status}"; then '
        s += '  exit 1; '
        s += 'fi; '
    s += f'echo "Waiting for job {job_name} to be in {good_statuses}..."; '
    s += 'done'
    return s


def check_logs(job_id: int, expected_pattern: str):
    """Check that job logs contain the expected pattern.

    Args:
        job_id: The job ID to check logs for.
        expected_pattern: The pattern to grep for in the logs.
    """
    return (
        f'for attempt in 1 2; do '
        f'  logs=$(sky jobs logs --controller {job_id} --no-follow 2>&1); '
        f'  echo "$logs"; '
        f'  if echo "$logs" | grep "{expected_pattern}"; then '
        f'    echo "Job {job_id} logs contain expected pattern: {expected_pattern}"; '
        f'    exit 0; '
        f'  fi; '
        f'  if [ $attempt -eq 1 ]; then '
        f'    echo "Pattern not found on attempt $attempt, retrying in 5 seconds..."; '
        f'    sleep 5; '
        f'  fi; '
        f'done; '
        f'echo "ERROR: Job {job_id} logs do not contain expected pattern: {expected_pattern} after 2 attempts"; '
        f'exit 1')


def wait_until_job_status_by_id(
        job_id: int,
        good_statuses: List[str],
        bad_statuses: List[str] = ['CANCELLED', 'FAILED_CONTROLLER'],
        timeout: int = 30):
    s = 'start_time=$SECONDS; '
    s += 'while true; do '
    s += f'if (( $SECONDS - $start_time > {timeout} )); then '
    s += f'  echo "Timeout after {timeout} seconds waiting for job {job_id} to succeed"; '
    s += '  echo "=== Running sky status for debugging ==="; '
    s += '  sky status || true; '
    s += '  exit 1; '
    s += 'fi; '
    s += f's=$(sky jobs logs --controller {job_id} --no-follow); '
    s += 'echo "$s"; '
    for status in good_statuses:
        s += f'if echo "$s" | grep "Job status: JobStatus.{status}"; then '
        s += '  break; '
        s += 'fi; '
    for status in bad_statuses:
        s += f'if echo "$s" | grep "Job status: JobStatus.{status}"; then '
        s += '  echo "=== Running sky status for debugging ==="; '
        s += '  sky status || true; '
        s += '  exit 1; '
        s += 'fi; '
    s += f'echo "Waiting for job {job_id} to be in {good_statuses}..."; '
    s += 'done'
    return s


def check_num_running_jobs(job_names: List[str],
                           expected_count: int,
                           timeout: int = 30):
    """Check that exactly expected_count jobs are in RUNNING state.

    Args:
        job_names: List of job names to check.
        expected_count: Expected number of jobs in RUNNING state.
        timeout: Timeout in seconds.
    """
    job_names_str = ' '.join(job_names)
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for {expected_count} jobs to be RUNNING"; '
        '  echo "=== Current job status ==="; '
        '  sky jobs queue; '
        '  exit 1; '
        'fi; '
        's=$(sky jobs queue); '
        'echo "$s"; '
        'running_count=0; '
        f'for job_name in {job_names_str}; do '
        '  if echo "$s" | grep "$job_name" | grep "RUNNING"; then '
        '    running_count=$((running_count + 1)); '
        '  fi; '
        'done; '
        f'if [ "$running_count" -eq {expected_count} ]; then '
        f'  echo "Found exactly {expected_count} jobs in RUNNING state"; '
        '  break; '
        'fi; '
        f'echo "Found $running_count jobs in RUNNING state, expected {expected_count}"; '
        'sleep 2; '
        'done')


def wait_until_num_workers(pool_name: str,
                           num_workers: int,
                           timeout: int = 30,
                           time_between_checks: int = 5):
    status_str = f'sky jobs pool status {pool_name} | grep "^{pool_name}"'

    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for num workers {num_workers}"; exit 1; '
        'fi; '
        f's=$({status_str}); '
        'echo "$s"; '
        f'if echo "$s" | grep "{num_workers}/{num_workers}"; then '
        '  break; '
        'fi; '
        'if echo "$s" | grep "FAILED"; then '
        '  exit 1; '
        'fi; '
        f'echo "Waiting for num workers to be {num_workers}..."; '
        f'sleep {time_between_checks}; '
        'done')


def check_for_setup_message(pool_name: str,
                            setup_message: str,
                            follow: bool = True):
    return (
        f's=$(sky jobs pool logs {pool_name} 1 {"--no-follow" if not follow else ""}); '
        f'echo "$s"; echo; echo; echo "$s" | grep "{setup_message}"')


def check_for_recovery_message_on_controller(job_name: str):
    return (f's=$(sky jobs logs --controller -n {job_name} --no-follow); '
            f'echo "$s"; echo; echo; echo "$s" | grep "RECOVERING"')


def wait_for_message_in_pool_logs(pool_name: str,
                                  message: str,
                                  timeout: int = 300,
                                  time_between_checks: int = 10):
    """Wait for a specific message to appear in pool logs.
    
    Args:
        pool_name: Name of the pool to check logs for.
        message: The message to search for in the logs (case-insensitive).
        timeout: Maximum time to wait in seconds.
        time_between_checks: Time to wait between checks in seconds.
    """
    num_checks = timeout // time_between_checks
    return (
        f'for i in {{1..{num_checks}}}; do '
        f'logs=$(sky jobs pool logs --controller {pool_name} --no-follow 2>&1); '
        'echo "$logs"; '
        f'if echo "$logs" | grep -i "{message}"; then '
        f'  echo "Found {message} in logs"; '
        '  exit 0; '
        'fi; '
        f'echo "Check $i/{num_checks}: {message} not found yet"; '
        f'sleep {time_between_checks}; '
        'done; '
        f'echo "ERROR: {message} not found in logs after timeout"; '
        'exit 1')


def wait_for_message_in_pool_logs(pool_name: str,
                                  message: str,
                                  timeout: int = 300,
                                  time_between_checks: int = 10):
    """Wait for a specific message to appear in pool logs.
    
    Args:
        pool_name: Name of the pool to check logs for.
        message: The message to search for in the logs (case-insensitive).
        timeout: Maximum time to wait in seconds.
        time_between_checks: Time to wait between checks in seconds.
    """
    num_checks = timeout // time_between_checks
    return (
        f'for i in {{1..{num_checks}}}; do '
        f'logs=$(sky jobs pool logs --controller {pool_name} --no-follow 2>&1); '
        'echo "$logs"; '
        f'if echo "$logs" | grep -i "{message}"; then '
        f'  echo "Found {message} in logs"; '
        '  exit 0; '
        'fi; '
        f'echo "Check $i/{num_checks}: {message} not found yet"; '
        f'sleep {time_between_checks}; '
        'done; '
        f'echo "ERROR: {message} not found in logs after timeout"; '
        'exit 1')


def basic_pool_conf(
    num_workers: int,
    infra: str,
    accelerator_string: Optional[str] = None,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    setup_cmd: str = 'echo "setup message"',
    workdir: Optional[str] = None,
):
    workdir_section = f'workdir: {workdir}\n' if workdir else ''
    accelerator_string = '    accelerators: ' + accelerator_string if accelerator_string else ''
    cpus_string = '    cpus: ' + cpus if cpus else ''
    memory_string = '    memory: ' + memory if memory else ''
    return textwrap.dedent(f"""
    {workdir_section}
    pool:
        workers: {num_workers}

    resources:
        infra: {infra}
    {accelerator_string}
    {cpus_string}
    {memory_string}

    setup: |
        {setup_cmd}
    """)


def basic_job_conf(
    job_name: str,
    run_cmd: str = 'echo "run message"',
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
):
    resources_section = ''
    if cpus is not None or memory is not None:
        resources_section = '\n    resources:'
        if cpus is not None:
            resources_section += f'\n        cpus: {cpus}'
        if memory is not None:
            resources_section += f'\n        memory: {memory}'
    return textwrap.dedent(f"""
    name: {job_name}
{resources_section}
    run: |
        {run_cmd}
    """)


def unified_conf(num_workers: int,
                 infra: str,
                 resource_string: Optional[str] = None,
                 setup_cmd: str = 'echo "setup message"',
                 run_cmd: str = 'echo "run message"'):
    resource_string_section = f'        {resource_string}\n' if resource_string is not None else ''
    return textwrap.dedent(f"""
    pool:
        workers: {num_workers}

    resources:
        cpus: 2+
        memory: 4GB+
        infra: {infra}
{resource_string_section}
    setup: |
        {setup_cmd}

    run: |
        {run_cmd}
    """)


def write_yaml(yaml_file: tempfile.NamedTemporaryFile, config: str):
    yaml_file.write(config.encode())
    yaml_file.flush()


def get_worker_cluster_name(pool_name: str, worker_id: int):
    return common_utils.make_cluster_name_on_cloud(
        f'{pool_name}-{worker_id}', sky.AWS.max_cluster_name_length())


@pytest.mark.resource_heavy
@pytest.mark.parametrize('accelerator', [{'do': 'H100', 'nebius': 'L40S'}])
@pytest.mark.skip(
    'Skipping vllm pool test until more remote server testing is done.')
@pytest.mark.no_remote_server  # see note 1 above
def test_vllm_pool(generic_cloud: str, accelerator: Dict[str, str]):
    if generic_cloud == 'kubernetes':
        accelerator = smoke_tests_utils.get_available_gpus()
    else:
        accelerator = accelerator.get(generic_cloud, 'T4')
    name = smoke_tests_utils.get_cluster_name()
    pool_config = textwrap.dedent(f"""
    envs:
        MODEL_NAME: NousResearch/Meta-Llama-3-8B-Instruct

    resources:
        accelerators: {{{accelerator}}}
        infra: {generic_cloud}

    setup: |
        uv venv --python 3.10 --seed
        source .venv/bin/activate

        # Install fschat and accelerate for chat completion
        git clone https://github.com/vllm-project/vllm.git || true
        uv pip install "vllm>=0.8.3"
        uv pip install numpy pandas requests tqdm datasets nltk
        uv pip install torch torchvision aiohttp
        uv pip install hf_transfer pyarrow

        echo 'Starting vllm api server...'
        # Use setsid to start vllm in a new session, completely detached from parent,
        # so that it is not killed by setup completion.
        setsid bash -c "vllm serve $MODEL_NAME --dtype auto > ./vllm.log 2>&1" > /dev/null 2>&1 &
        sleep 2  # Give it a moment to start
        echo "vLLM server started in detached session"

        # Wait for vLLM service to be ready by checking the health endpoint
        echo "Waiting for vLLM service to be ready..."
        while ! curl -s http://localhost:8000/health > /dev/null; do
            sleep 5
        echo "Still waiting for vLLM service..."
        done
        echo "vLLM service is ready!"



    pool:
        workers: 1
    """)

    bucket_name = f'sky-test-vllm-pool-{name}'

    job_config = textwrap.dedent(f"""
    name: t-test-vllm-pool

    resources:
        infra: {generic_cloud}

    envs:
        START_IDX: 0  # Will be overridden by batch launcher script
        END_IDX: 10000  # Will be overridden by batch launcher script
        BUCKET_NAME: {bucket_name}
        MODEL_NAME: "Alibaba-NLP/gte-Qwen2-7B-instruct"
        DATASET_NAME: "McAuley-Lab/Amazon-Reviews-2023"
        DATASET_CONFIG: "raw_review_Books"
        EMBEDDINGS_BUCKET_NAME: {bucket_name}
        WORKER_ID: ''

    file_mounts:
        /output:
            name: ${{EMBEDDINGS_BUCKET_NAME}}
            mode: MOUNT
            store: s3


    run: |
        source .venv/bin/activate

        # Initialize and download the model
        HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download --local-dir /tmp/model $MODEL_NAME

        # Create metrics directory for monitoring service
        mkdir -p /output/metrics

        # Set worker ID for metrics tracking
        if [ -z "$WORKER_ID" ]; then
            export WORKER_ID="worker_$(date +%s)_$(hostname)"
            echo "Generated worker ID: $WORKER_ID"
        fi

        # Process the assigned range of documents
        echo "Processing documents from $START_IDX to $END_IDX"

        # Process text documents and track token metrics
        python scripts/text_vector_processor.py \
            --output-path "/output/embeddings_${{START_IDX}}_${{END_IDX}}.parquet" \
            --start-idx $START_IDX \
            --end-idx $END_IDX \
            --chunk-size 512 \
            --chunk-overlap 50 \
            --vllm-endpoint http://localhost:8000 \
            --batch-size 32 \
            --model-name /tmp/model \
            --dataset-name $DATASET_NAME \
            --dataset-config $DATASET_CONFIG

        # Print tokens statistics summary from metrics
        echo "Embedding generation complete. Token statistics saved to metrics."
    """)

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_vllm_pool',
                [
                    f's=$(sky jobs pool apply -p {pool_name} {pool_yaml.name} -y); echo "$s"; echo; echo; echo "$s" | grep "Successfully created pool"',
                    wait_until_pool_ready(
                        pool_name,
                        timeout=smoke_tests_utils.get_timeout(generic_cloud)),
                    f's=$(sky jobs launch --pool {pool_name} {job_yaml.name} -y); echo "$s"; echo; echo; echo "$s" | grep "Job finished (status: SUCCEEDED)."',
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=(f'sky jobs pool down {pool_name} -y && '
                          f'sky storage delete -y {bucket_name} || true'),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_setup_logs_in_starting_pool(generic_cloud: str):
    """Test that setup logs are streamed in starting state."""
    # Do a very long setup so we know the setup logs are streamed in
    pool_config = basic_pool_conf(
        1,
        infra=generic_cloud,
        setup_cmd='for i in {1..10000}; do echo "Noisy setup $i"; sleep 1; done'
    )
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test('test_setup_logs_in_starting_pool', [
            _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                  pool_yaml=pool_yaml.name),
            wait_until_worker_status(pool_name, 'STARTING', timeout=timeout),
            check_for_setup_message(pool_name, 'Noisy setup 1', follow=False),
        ],
                                      timeout=timeout,
                                      teardown=_TEARDOWN_POOL.format(
                                          pool_name=pool_name))

        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_setup_logs_in_pool_exits(generic_cloud: str):
    """Test that setup logs are streamed and exit once the setup is complete."""
    """We omit --no-follow to test that we exit."""
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test(
            'test_setup_logs_in_starting_pool', [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_worker_status(pool_name, 'READY', timeout=timeout),
                check_for_setup_message(pool_name, 'setup message'),
            ],
            timeout=timeout,
            teardown=_TEARDOWN_POOL.format(pool_name=pool_name))

        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_update_workers(generic_cloud: str):
    """Test that we can update the number of workers in a pool, both
    up and down.
    """
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test(
            'test_update_workers',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
                _POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS.format(
                    pool_name=pool_name, num_workers=2),
                wait_until_num_workers(pool_name, 2, timeout=timeout),
                _POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS.format(
                    pool_name=pool_name, num_workers=1),
                # Shutting down takes a while, so we give it a longer timeout.
                wait_until_num_workers(pool_name, 1, timeout=timeout),
            ],
            timeout=timeout,
            teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_update_workers_and_yaml(generic_cloud: str):
    """Test that we error if the user specifies a yaml and --workers.
    """
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test('test_update_workers_and_yaml', [
            _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                  pool_yaml=pool_yaml.name),
            f's=$(sky jobs pool apply {pool_yaml.name} -p {pool_name} --workers 2 -y 2>&1); echo "$s"; echo; echo; echo "$s" | grep "Cannot specify both --workers and POOL_YAML"',
        ],
                                      timeout=timeout,
                                      teardown=_TEARDOWN_POOL.format(
                                          pool_name=pool_name))
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_update_workers_no_pool(generic_cloud: str):
    """Test that we error if the user specifies a yaml and --workers.
    """
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test('test_update_workers_and_yaml', [
            _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                  pool_yaml=pool_yaml.name),
            f's=$(sky jobs pool apply --workers 2 -y 2>&1); echo "$s"; echo; echo; echo "$s" | grep "A pool name must be provided to update the number of workers."',
        ],
                                      timeout=timeout,
                                      teardown=_TEARDOWN_POOL.format(
                                          pool_name=pool_name))
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_queueing(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1,
                                  infra=generic_cloud,
                                  setup_cmd='sleep infinity')

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_queueing',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the job.
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    # Ensure the job is pending.
                    wait_until_job_status(job_name, ['PENDING'],
                                          timeout=timeout),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.no_remote_server  # see note 1 above
def test_pool_preemption(generic_cloud: str):
    region = 'us-east-2'
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_name = common_utils.make_cluster_name_on_cloud(
        pool_name, sky.AWS.max_cluster_name_length())
    pool_config = basic_pool_conf(num_workers=1, infra=f"aws/{region}")

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    get_instance_id_cmd = smoke_tests_utils.AWS_GET_INSTANCE_ID.format(
        region=region, name_on_cloud=get_worker_cluster_name(pool_name, 1))
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)
            test = smoke_tests_utils.Test(
                'test_pool_preemption',
                [
                    smoke_tests_utils.launch_cluster_for_cloud_cmd(
                        'aws', name, skip_remote_server_check=True),
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    # Restart the cluster manually.
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=_TERMINATE_INSTANCE.format(
                            region=region,
                            get_instance_id_cmd=get_instance_id_cmd),
                        skip_remote_server_check=True),
                    'sky status --refresh',
                    # # Wait until job is running.
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    check_for_recovery_message_on_controller(job_name),
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=
                f'{cancel_jobs_and_teardown_pool(pool_name, timeout=10)} && '
                f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name, skip_remote_server_check=True)}',
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_running(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pools_job_cancel_running',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the job.
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    # Ensure the job is running.
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    # Cancel the job.
                    cancel_job(job_name),
                    # Ensure the job is cancelled.
                    wait_until_job_status(
                        job_name, ['CANCELLED'], bad_statuses=[], timeout=15),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_instant(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_instant',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the job.
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    # Cancel the job.
                    cancel_job(job_name),
                    # Ensure the job is cancelled.
                    wait_until_job_status(
                        job_name, ['CANCELLED'], bad_statuses=[], timeout=15),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_recovery(generic_cloud: str):
    region = 'us-east-2'
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_name = common_utils.make_cluster_name_on_cloud(
        pool_name, sky.AWS.max_cluster_name_length())
    pool_config = basic_pool_conf(num_workers=1, infra=f"aws/{region}")

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    get_instance_id_cmd = smoke_tests_utils.AWS_GET_INSTANCE_ID.format(
        region=region, name_on_cloud=get_worker_cluster_name(pool_name, 1))
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)
            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_recovery',
                [
                    smoke_tests_utils.launch_cluster_for_cloud_cmd(
                        'aws', name, skip_remote_server_check=True),
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    # Restart the cluster manually.
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=_TERMINATE_INSTANCE.format(
                            region=region,
                            get_instance_id_cmd=get_instance_id_cmd),
                        skip_remote_server_check=True),
                    'sky status --refresh',
                    wait_until_job_status(job_name, ['RUNNING', 'RECOVERING'],
                                          timeout=timeout,
                                          time_between_checks=1),
                    check_for_recovery_message_on_controller(job_name),
                    # Cancel the job.
                    cancel_job(job_name),
                    # Ensure the job is cancelled.
                    wait_until_job_status(
                        job_name, ['CANCELLED'], bad_statuses=[], timeout=15),
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=
                f'{cancel_jobs_and_teardown_pool(pool_name, timeout=10)} && '
                f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name, skip_remote_server_check=True)}',
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_running_multiple(generic_cloud: str):
    num_jobs = 4
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=num_jobs, infra=generic_cloud)
    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_running_multiple',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the jobs.
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Ensure the jobs are running.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['RUNNING'],
                                              timeout=timeout)
                        for i in range(1, num_jobs + 1)
                    ],
                    # Cancel the jobs.
                    *[
                        cancel_job(f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Ensure the jobs are cancelled.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=15)
                        for i in range(1, num_jobs + 1)
                    ],
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_running_multiple_simultaneous(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    num_jobs = 4
    pool_config = basic_pool_conf(num_workers=num_jobs, infra=generic_cloud)

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_running_multiple_simultaneous',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the jobs.
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Ensure the jobs are running.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['RUNNING'],
                                              timeout=timeout)
                        for i in range(1, num_jobs + 1)
                    ],
                    # Cancel all jobs at once.
                    _CANCEL_POOL_JOBS.format(pool_name=pool_name),
                    # Ensure the jobs are cancelled.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=15)
                        for i in range(1, num_jobs + 1)
                    ],
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_instant_multiple(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    num_jobs = 4
    pool_config = basic_pool_conf(num_workers=num_jobs, infra=generic_cloud)
    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_instant_multiple',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the jobs.
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Cancel the jobs immediately.
                    *[
                        cancel_job(f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Ensure the jobs are cancelled.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=15)
                        for i in range(1, num_jobs + 1)
                    ],
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_job_cancel_instant_multiple_simultaneous(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    num_jobs = 4
    pool_config = basic_pool_conf(num_workers=num_jobs, infra=generic_cloud)
    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_job_cancel_instant_multiple_simultaneous',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Immediately attempt to launch the jobs.
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}')
                        for i in range(1, num_jobs + 1)
                    ],
                    # Cancel all jobs at once.
                    _CANCEL_POOL_JOBS.format(pool_name=pool_name),
                    # Ensure the jobs are cancelled.
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=15)
                        for i in range(1, num_jobs + 1)
                    ],
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_job_cancel_no_jobs(generic_cloud: str):
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)

        name = smoke_tests_utils.get_cluster_name()
        pool_name = f'{name}-pool'

        test = smoke_tests_utils.Test(
            'test_pools_job_cancel_running',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                # Cancel the job.
                f's=$(sky jobs cancel --pool {pool_name} -y 2>&1); echo "$s"; echo; echo; echo "$s" | grep "No running job found in pool"',
            ],
            timeout=timeout,
            teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_num_jobs_basic(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    num_jobs = 2
    job_config = basic_job_conf(
        job_name=f'{name}-job',
        run_cmd='echo "Running with $SKYPILOT_NUM_JOBS jobs"')
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)
            job_ids = list(range(1, 1 + num_jobs))
            test = smoke_tests_utils.Test(
                'test_pools_num_jobs',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    f'sky jobs launch --pool {pool_name} {job_yaml.name} --num-jobs {num_jobs} -d -y',
                    # Wait for the jobs to succeed.
                    *[
                        wait_until_job_status_by_id(job_id, ['SUCCEEDED'],
                                                    timeout=timeout)
                        for job_id in job_ids
                    ],
                    # Sleep to ensure the job logs are ready.
                    'sleep 30',
                    # Check that the job logs contain the correct number of jobs.
                    *[
                        check_logs(job_id, f'Running with {num_jobs} jobs')
                        for job_id in job_ids
                    ],
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_worker_assignment_in_queue(generic_cloud: str):
    """Test that sky jobs queue shows the worker assignment for running jobs."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    job_name = f'{smoke_tests_utils.get_cluster_name()}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Hello, world!"; sleep infinity',
    )
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            name = smoke_tests_utils.get_cluster_name()
            pool_name = f'{name}-pool'

            test = smoke_tests_utils.Test(
                'test_pool_worker_assignment_in_queue',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    # Check that the worker assignment is shown in the queue output
                    f's=$(sky jobs queue); echo "$s"; echo; echo; echo "$s" | grep "{job_name}" | grep "{pool_name} (worker=1)"',
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_num_jobs_option(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    job_config = basic_job_conf(job_name=f'{name}-job',)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)
            test = smoke_tests_utils.Test(
                'test_pools_num_jobs',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Test parallel job launching with --num-jobs 3
                    ('s=$(sky jobs launch --pool {pool_name} {job_yaml} --num-jobs 10 -d -y); '
                     'echo "$s"; '
                     'echo; echo; echo "$s" | grep "Jobs submitted with IDs: 2,3,4,5,6,7,8,9,10,11"; '
                     'sleep 5').format(pool_name=pool_name,
                                       job_yaml=job_yaml.name)
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
@pytest.mark.no_remote_server  # see note 1 above
def test_pools_setup_num_gpus(generic_cloud: str):
    """Test that the number of GPUs is set correctly in the setup script."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    setup_cmd = 'if [[ "$SKYPILOT_SETUP_NUM_GPUS_PER_NODE" != "2" ]]; then exit 1; fi'
    pool_config = basic_pool_conf(num_workers=1,
                                  infra=generic_cloud,
                                  accelerator_string='{L4:2}',
                                  setup_cmd=setup_cmd)

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        test = smoke_tests_utils.Test(
            'test_pools_setup_num_gpus',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                # Wait for the pool to be created.
                wait_until_pool_ready(pool_name, timeout=timeout),
            ],
            timeout=timeout,
            teardown=_TEARDOWN_POOL.format(pool_name=pool_name))
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_single_yaml(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    job_name = f'{name}-job'
    one_config = unified_conf(num_workers=1,
                              infra=generic_cloud,
                              setup_cmd='echo "setup message"',
                              run_cmd='echo "Unified job"')
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as one_config_yaml:
        write_yaml(one_config_yaml, one_config)
        test = smoke_tests_utils.Test(
            'test_pools_single_yaml',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                    pool_name=pool_name, pool_yaml=one_config_yaml.name),
                f'sky jobs launch --pool {pool_name} {one_config_yaml.name} --name {job_name} -d -y',
                wait_until_job_status(job_name, ['SUCCEEDED'], timeout=timeout),
            ],
            timeout=smoke_tests_utils.get_timeout(generic_cloud),
            teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.resource_heavy
@pytest.mark.no_remote_server  # see note 1 above
@pytest.mark.no_kubernetes  # Kubernetes may not have multiple GPU types
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
@pytest.mark.no_hyperbolic  # Hyperbolic only supports one GPU type per instance
@pytest.mark.no_seeweb  # Seeweb does not support T4
def test_pools_heterogeneous_any_of(generic_cloud: str):
    """Test pools with heterogeneous resources using any_of accelerators."""
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    job_name = f'{name}-job'
    # Use any_of with cheaper GPUs (T4 and V100 are commonly available)
    one_config = unified_conf(
        num_workers=1,
        infra=generic_cloud,
        resource_string="accelerators: {'T4:1', 'V100:1'}",
        setup_cmd='echo "setup message"; nvidia-smi',
        run_cmd='echo "Heterogeneous job"; nvidia-smi')
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as one_config_yaml:
        write_yaml(one_config_yaml, one_config)
        test = smoke_tests_utils.Test(
            'test_pools_heterogeneous_any_of',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                    pool_name=pool_name, pool_yaml=one_config_yaml.name),
                (f's=$(sky jobs launch --pool {pool_name} {one_config_yaml.name} --name {job_name} -d -y); '
                 'echo "$s"; '
                 'echo; echo; echo "$s" | grep "Job submitted"'),
                wait_until_job_status(job_name, ['SUCCEEDED'], timeout=timeout),
            ],
            timeout=smoke_tests_utils.get_timeout(generic_cloud),
            teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
@pytest.mark.resource_heavy
@pytest.mark.no_remote_server  # see note 1 above
def test_pools_heterogeneous_resource_scheduling(generic_cloud: str):
    """Test resource-aware scheduling with heterogeneous job requirements.

    This test validates that jobs with any_of resources (T4 or A100) can be
    scheduled on a worker with only T4s. The scheduler should recognize that
    T4s are available and schedule jobs accordingly, even though A100s are
    also specified as an option.

    Test scenario:
    - Pool: 1 worker with g4dn.12xlarge (4 T4 GPUs)
    - Job: Requests any_of T4:1 or A100:1
    - Launch 5 jobs that sleep forever
    - Expected: 4 jobs run, 1 job waits (since only 4 T4s are available)
    """
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Create pool with g4dn.12xlarge (has 4 T4 GPUs)
    pool_config = textwrap.dedent(f"""
    pool:
        workers: 1

    resources:
        instance_type: g4dn.12xlarge
        infra: aws

    setup: |
        echo "Pool worker setup complete"
    """)

    # Create job that requests either T4 or A100 (any_of)
    job_name_prefix = f'{name}-job'
    job_config = textwrap.dedent(f"""
    name: {job_name_prefix}

    resources:
        accelerators: {{'T4:1', 'A100:1'}}

    run: |
        echo "Job running with GPU:"
        nvidia-smi --query-gpu=name --format=csv,noheader
        sleep infinity
    """)

    timeout = smoke_tests_utils.get_timeout(generic_cloud)

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml, \
         tempfile.NamedTemporaryFile(delete=True) as job_yaml:
        write_yaml(pool_yaml, pool_config)
        write_yaml(job_yaml, job_config)

        # Build test commands
        test_cmds = [
            _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                  pool_yaml=pool_yaml.name),
            wait_until_pool_ready(pool_name, timeout=timeout),
        ]

        # Launch first 4 jobs and wait for each to be RUNNING
        for i in range(4):
            job_name = f'{job_name_prefix}-{i}'
            test_cmds.extend([
                f's=$(sky jobs launch --pool {pool_name} {job_yaml.name} '
                f'--name {job_name} -d -y); '
                f'echo "$s"; '
                f'echo; echo; echo "$s" | grep "Job submitted"',
                wait_until_job_status(job_name, ['RUNNING'], timeout=timeout),
            ])

        # Launch 5th job
        fifth_job_name = f'{job_name_prefix}-4'
        test_cmds.extend([
            f's=$(sky jobs launch --pool {pool_name} {job_yaml.name} '
            f'--name {fifth_job_name} -d -y); '
            f'echo "$s"; '
            f'echo; echo; echo "$s" | grep "Job submitted"',
            # Wait for it to be PENDING
            wait_until_job_status(fifth_job_name, ['PENDING'],
                                  bad_statuses=[],
                                  timeout=timeout),
            # Sleep 60 seconds
            'sleep 60',
            # Verify it's still PENDING or WAITING (not running)
            wait_until_job_status(fifth_job_name, ['PENDING'],
                                  bad_statuses=['RUNNING'],
                                  timeout=30),
        ])

        test = smoke_tests_utils.Test(
            'test_pools_heterogeneous_resource_scheduling',
            test_cmds,
            timeout=smoke_tests_utils.get_timeout(generic_cloud) * 2,
            teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=10),
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_double_launch(generic_cloud: str):
    """Test that we can launch a pool with the same name twice.
    """
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'
        test = smoke_tests_utils.Test(
            'test_double_launch',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
                _TEARDOWN_POOL.format(pool_name=pool_name),
                'sleep 60',  # Wait a little bit to ensure the pool is fully shut down.
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
            ],
            timeout=timeout,
            teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
        )
        smoke_tests_utils.run_one_test(test)


def check_pool_not_in_status(pool_name: str,
                             timeout: int = 30,
                             time_between_checks: int = 5):
    """Check that a pool does not appear in `sky jobs pool status`.

    Args:
        pool_name: The name of the pool to check for.
        timeout: Maximum time in seconds to wait for the pool to be removed.
        time_between_checks: Time in seconds to wait between checks.
    """
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for pool {pool_name} to be removed"; '
        f'  s=$(sky jobs pool status); '
        f'  echo "$s"; '
        f'  if echo "$s" | grep "{pool_name}"; then '
        f'    echo "ERROR: Pool {pool_name} still exists in pool status"; '
        f'    exit 1; '
        f'  fi; '
        f'  exit 0; '
        'fi; '
        f's=$(sky jobs pool status); '
        'echo "$s"; '
        f'if ! echo "$s" | grep "{pool_name}"; then '
        f'  echo "Pool {pool_name} correctly removed from pool status"; '
        '  break; '
        'fi; '
        f'echo "Waiting for pool {pool_name} to be removed..."; '
        f'sleep {time_between_checks}; '
        'done')


@pytest.mark.resource_heavy
@pytest.mark.no_remote_server  # see note 1 above
def test_pool_down_all_with_running_jobs(generic_cloud: str):
    """Test that `sky jobs pool down -a -y` cancels running jobs and removes pools.

    This test:
    1. Launches two pools with the same config but different names
    2. Launches 1 job (sleeping for a long time) to each pool (2 jobs total)
    3. Runs `sky jobs pool down -a -y` to down both pools
    4. Verifies each job has 'CANCELLED' status
    5. Verifies the pools don't show in `sky jobs pool status`
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    name = smoke_tests_utils.get_cluster_name()
    pool_name_1 = f'{name}-pool-1'
    pool_name_2 = f'{name}-pool-2'

    # Create job configs with long-running sleep commands
    job_name_1 = f'{name}-job-1'
    job_name_2 = f'{name}-job-2'

    job_config = basic_job_conf(
        job_name=job_name_1,  # Name will be overridden with -n flag
        run_cmd='sleep infinity',
    )

    # Configure jobs controller resources: 4 cores and 20GB memory
    controller_config = {
        'jobs': {
            'controller': {
                'resources': {
                    'cpus': '4+',
                    'memory': '32+',
                }
            }
        }
    }

    with smoke_tests_utils.override_sky_config(config_dict=controller_config):
        with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
            with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
                write_yaml(pool_yaml, pool_config)
                write_yaml(job_yaml, job_config)

                test = smoke_tests_utils.Test(
                    'test_pool_down_all_with_running_jobs',
                    [
                        _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name_1, pool_yaml=pool_yaml.name),
                        wait_until_pool_ready(pool_name_1, timeout=timeout),
                        _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name_2, pool_yaml=pool_yaml.name),
                        wait_until_pool_ready(pool_name_2, timeout=timeout),
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name_1,
                            job_yaml=job_yaml.name,
                            job_name=job_name_1),
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name_2,
                            job_yaml=job_yaml.name,
                            job_name=job_name_2),
                        wait_until_job_status(job_name_1, ['RUNNING'],
                                              timeout=timeout),
                        wait_until_job_status(job_name_2, ['RUNNING'],
                                              timeout=timeout),
                        'sky jobs pool down -a -y',
                        # Wait a bit for cancellation to propagate
                        'sleep 10',
                        wait_until_job_status(job_name_1, ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=30),
                        wait_until_job_status(job_name_2, ['CANCELLED'],
                                              bad_statuses=[],
                                              timeout=30),
                        check_pool_not_in_status(pool_name_1),
                        check_pool_not_in_status(pool_name_2),
                    ],
                    timeout=timeout,
                    teardown=cancel_jobs_and_teardown_pool(pool_name_1,
                                                           timeout=5) +
                    cancel_jobs_and_teardown_pool(pool_name_2, timeout=5),
                )
                smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_down_single_pool(generic_cloud: str):
    """Test that `sky jobs pool down <pool_name> -y` downs a single pool.

    This test:
    1. Launches one pool
    2. Launches 1 job (sleeping for a long time) to the pool
    3. Runs `sky jobs pool down <pool_name> -y` to down the pool
    4. Verifies the job has 'CANCELLED' status
    5. Verifies the pool doesn't show in `sky jobs pool status`
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Create job config with long-running sleep command
    job_name = f'{name}-job'

    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='sleep infinity',
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            test = smoke_tests_utils.Test(
                'test_pool_down_single_pool',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                        pool_name=pool_name,
                        job_yaml=job_yaml.name,
                        job_name=job_name),
                    wait_until_job_status(job_name, ['RUNNING'],
                                          timeout=timeout),
                    _TEARDOWN_POOL.format(pool_name=pool_name),
                    # Wait a bit for cancellation to propagate
                    'sleep 10',
                    wait_until_job_status(
                        job_name, ['CANCELLED'], bad_statuses=[], timeout=30),
                    check_pool_not_in_status(pool_name, timeout=30),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_scale_with_workdir(generic_cloud: str):
    """Test that we can scale a pool with workdir without errors. This makes
    sure that the workdir is not deleted when the pool is scaled."""

    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    timeout = smoke_tests_utils.get_timeout(generic_cloud)

    # Create a temporary directory with a file in it to use as workdir
    with tempfile.TemporaryDirectory(
            prefix='sky-test-workdir-') as temp_workdir:
        test_file_path = os.path.join(temp_workdir, 'test_file.txt')
        with open(test_file_path, 'w') as f:
            f.write('test content')

        pool_config = basic_pool_conf(
            num_workers=1,
            infra=generic_cloud,
            workdir=temp_workdir,
            setup_cmd='echo "setup message"',
        )

        with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
            write_yaml(pool_yaml, pool_config)
            test = smoke_tests_utils.Test(
                'test_pool_scale_down_with_workdir',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_worker_status(
                        pool_name, 'READY', timeout=timeout, num_occurrences=1),
                    _POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, num_workers=2),
                    wait_until_worker_status(
                        pool_name, 'READY', timeout=timeout, num_occurrences=2),
                    _POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, num_workers=1),
                    wait_until_worker_status(
                        pool_name, 'READY', timeout=timeout, num_occurrences=1),
                ],
                timeout=timeout,
                teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_resource_multiple_jobs_single_worker(generic_cloud: str):
    """Test that multiple jobs can run on a single worker when resources allow."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 4 CPUs and 8GB memory, single worker
    pool_config = basic_pool_conf(
        num_workers=1,
        infra=generic_cloud,
        cpus='4+',
        memory='8GB+',
    )

    # Two jobs, each taking 2 CPUs and 4GB memory
    job_name_1 = f'{name}-job-1'
    job_name_2 = f'{name}-job-2'
    job_config_1 = basic_job_conf(
        job_name=job_name_1,
        run_cmd='echo "Job 1 running" && sleep infinity',
        cpus='2',
        memory='4GB',
    )
    job_config_2 = basic_job_conf(
        job_name=job_name_2,
        run_cmd='echo "Job 2 running" && sleep infinity',
        cpus='2',
        memory='4GB',
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml_1:
            with tempfile.NamedTemporaryFile(delete=True) as job_yaml_2:
                write_yaml(pool_yaml, pool_config)
                write_yaml(job_yaml_1, job_config_1)
                write_yaml(job_yaml_2, job_config_2)

                test = smoke_tests_utils.Test(
                    'test_pool_resource_multiple_jobs_single_worker',
                    [
                        _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, pool_yaml=pool_yaml.name),
                        wait_until_pool_ready(pool_name, timeout=timeout),
                        # Launch first job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, job_yaml=job_yaml_1.name),
                        # Launch second job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, job_yaml=job_yaml_2.name),
                        # Wait for both jobs to be RUNNING
                        wait_until_job_status(job_name_1, ['RUNNING'],
                                              timeout=timeout),
                        wait_until_job_status(job_name_2, ['RUNNING'],
                                              timeout=timeout),
                    ],
                    timeout=timeout,
                    teardown=cancel_jobs_and_teardown_pool(pool_name,
                                                           timeout=5),
                )

                smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_resource_contention_two_workers(generic_cloud: str):
    """Test that only one job runs when resources don't allow both."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 2 CPUs and 8GB memory, single worker
    pool_config = basic_pool_conf(
        num_workers=2,
        infra=generic_cloud,
        cpus='2',
        memory='8GB',
    )

    def _get_job_config(job_name: str) -> str:
        return basic_job_conf(
            job_name=job_name,
            run_cmd=f'echo "Job {job_name} running" && sleep infinity',
            cpus='2',
            memory='8GB',
        )

    num_jobs = 4

    # Four jobs, each taking 2 CPUs and 8GB memory (only two can fit)
    job_names = [f'{name}-job-{i}' for i in range(num_jobs)]
    job_configs = [_get_job_config(job_name) for job_name in job_names]

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml_1:
            with tempfile.NamedTemporaryFile(delete=True) as job_yaml_2:
                with tempfile.NamedTemporaryFile(delete=True) as job_yaml_3:
                    with tempfile.NamedTemporaryFile(delete=True) as job_yaml_4:

                        write_yaml(pool_yaml, pool_config)
                        yamls = [job_yaml_1, job_yaml_2, job_yaml_3, job_yaml_4]
                        for job_yaml, job_config in zip(yamls, job_configs):
                            write_yaml(job_yaml, job_config)

                        test = smoke_tests_utils.Test(
                            'test_pool_resource_contention_two_workers',
                            [
                                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                                    pool_name=pool_name,
                                    pool_yaml=pool_yaml.name),
                                wait_until_pool_ready(pool_name,
                                                      timeout=timeout),
                                # Launch all jobs
                                *[
                                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                                        pool_name=pool_name,
                                        job_yaml=job_yaml.name)
                                    for job_yaml in yamls
                                ],
                                # Verify only one job is RUNNING
                                check_num_running_jobs(
                                    job_names, 2, timeout=timeout),
                                # Wait 30 seconds
                                'sleep 30',
                                # Verify only one job is RUNNING
                                check_num_running_jobs(job_names, 2,
                                                       timeout=30),
                            ],
                            timeout=timeout,
                            teardown=cancel_jobs_and_teardown_pool(pool_name,
                                                                   timeout=5),
                        )

                        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_resource_contention_two_workers_some_available(
        generic_cloud: str):
    """Test that only one job runs when one resource allows both jobs to run but
    the other doesn't."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 2 CPUs and 8GB memory, single worker
    pool_config = basic_pool_conf(
        num_workers=2,
        infra=generic_cloud,
        cpus='2',
        memory='8GB',
    )

    def _get_job_config(job_name: str) -> str:
        return basic_job_conf(
            job_name=job_name,
            run_cmd=f'echo "Job {job_name} running" && sleep infinity',
            cpus='2',
            memory='4GB',
        )

    num_jobs = 4

    # Four jobs, each taking 2 CPUs and 4GB memory (only two can fit)
    job_names = [f'{name}-job-{i}' for i in range(num_jobs)]
    job_configs = [_get_job_config(job_name) for job_name in job_names]

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml_1:
            with tempfile.NamedTemporaryFile(delete=True) as job_yaml_2:
                with tempfile.NamedTemporaryFile(delete=True) as job_yaml_3:
                    with tempfile.NamedTemporaryFile(delete=True) as job_yaml_4:

                        write_yaml(pool_yaml, pool_config)
                        yamls = [job_yaml_1, job_yaml_2, job_yaml_3, job_yaml_4]
                        for job_yaml, job_config in zip(yamls, job_configs):
                            write_yaml(job_yaml, job_config)

                        test = smoke_tests_utils.Test(
                            'test_pool_resource_contention_two_workers',
                            [
                                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                                    pool_name=pool_name,
                                    pool_yaml=pool_yaml.name),
                                wait_until_pool_ready(pool_name,
                                                      timeout=timeout),
                                # Launch all jobs
                                *[
                                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                                        pool_name=pool_name,
                                        job_yaml=job_yaml.name)
                                    for job_yaml in yamls
                                ],
                                # Verify only one job is RUNNING
                                check_num_running_jobs(
                                    job_names, 2, timeout=timeout),
                                # Wait 30 seconds
                                'sleep 30',
                                # Verify only one job is RUNNING
                                check_num_running_jobs(job_names, 2,
                                                       timeout=30),
                            ],
                            timeout=timeout,
                            teardown=cancel_jobs_and_teardown_pool(pool_name,
                                                                   timeout=5),
                        )

                        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_resource_reclamation(generic_cloud: str):
    """Test that resources are reclaimed when jobs finish, allowing queued jobs to run."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 2 CPUs and 8GB memory, single worker
    pool_config = basic_pool_conf(
        num_workers=1,
        infra=generic_cloud,
        cpus='2',
        memory='8GB',
    )

    # Two jobs, each taking 2 CPUs and 8GB memory (can't both fit initially)
    job_name_1 = f'{name}-job-1'
    job_name_2 = f'{name}-job-2'
    job_config_1 = basic_job_conf(
        job_name=job_name_1,
        run_cmd='echo "hi"',
        cpus='2',
        memory='8GB',
    )
    job_config_2 = basic_job_conf(
        job_name=job_name_2,
        run_cmd='echo "hi"',
        cpus='2',
        memory='8GB',
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml_1:
            with tempfile.NamedTemporaryFile(delete=True) as job_yaml_2:
                write_yaml(pool_yaml, pool_config)
                write_yaml(job_yaml_1, job_config_1)
                write_yaml(job_yaml_2, job_config_2)

                test = smoke_tests_utils.Test(
                    'test_pool_resource_reclamation',
                    [
                        _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, pool_yaml=pool_yaml.name),
                        wait_until_pool_ready(pool_name, timeout=timeout),
                        # Launch first job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, job_yaml=job_yaml_1.name),
                        # Launch second job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, job_yaml=job_yaml_2.name),
                        # Wait for first job to succeed
                        wait_until_job_status(job_name_1, ['SUCCEEDED'],
                                              timeout=timeout),
                        # Wait for second job to succeed (should run after first finishes)
                        wait_until_job_status(job_name_2, ['SUCCEEDED'],
                                              timeout=timeout),
                    ],
                    timeout=timeout,
                    teardown=cancel_jobs_and_teardown_pool(pool_name,
                                                           timeout=5),
                )

                smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_resource_fallback_to_unaware(generic_cloud: str):
    """Test that resources are reclaimed when jobs finish, allowing queued jobs to run."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 2 CPUs and 8GB memory, single worker
    pool_config = basic_pool_conf(
        num_workers=1,
        infra=generic_cloud,
        cpus='2',
        memory='8GB',
    )

    # Two jobs, each taking 2 CPUs and 8GB memory (can't both fit initially)
    resource_aware_job_name = f'{name}-job-1'
    resource_unaware_job_name = f'{name}-job-2'

    resource_aware_job_config = basic_job_conf(
        job_name=resource_aware_job_name,
        run_cmd='echo "hi"',
        cpus='2',
        memory='8GB',
    )
    resource_unaware_job_config = basic_job_conf(
        job_name=resource_unaware_job_name,
        run_cmd='sleep 300',
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(
                delete=True) as resource_aware_job_yaml:
            with tempfile.NamedTemporaryFile(
                    delete=True) as resource_unaware_job_yaml:
                write_yaml(pool_yaml, pool_config)
                write_yaml(resource_aware_job_yaml, resource_aware_job_config)
                write_yaml(resource_unaware_job_yaml,
                           resource_unaware_job_config)

                test = smoke_tests_utils.Test(
                    'test_pool_resource_fallback_to_unaware',
                    [
                        _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name, pool_yaml=pool_yaml.name),
                        wait_until_pool_ready(pool_name, timeout=timeout),
                        # Launch resource unaware job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name,
                            job_yaml=resource_unaware_job_yaml.name),
                        wait_until_job_status(resource_unaware_job_name,
                                              ['RUNNING'],
                                              timeout=timeout),
                        # Launch second job
                        _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                            pool_name=pool_name,
                            job_yaml=resource_aware_job_yaml.name),

                        # Make sure it's pending.
                        wait_until_job_status(resource_aware_job_name,
                                              ['PENDING'],
                                              timeout=timeout),

                        # Now we sleep to give time for potentital scheduling.
                        'sleep 30',
                        # The job should still be pending.
                        wait_until_job_status(
                            resource_aware_job_name, ['PENDING'], timeout=1),
                    ],
                    timeout=timeout,
                    teardown=cancel_jobs_and_teardown_pool(pool_name,
                                                           timeout=5),
                )

                smoke_tests_utils.run_one_test(test)


@pytest.mark.resource_heavy
@pytest.mark.gcp
@pytest.mark.no_remote_server  # see note 1 above
def test_pool_fractional_gpu_scheduling(generic_cloud: str):
    """Test that 6 jobs requesting 0.5 L4 each can run on a pool with 3 workers each with 1 L4."""
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with 3 workers, each with 1 L4 GPU
    pool_config = basic_pool_conf(
        num_workers=3,
        infra=generic_cloud,
        accelerator_string='{L4:1}',
    )

    # Create 6 jobs, each requesting 0.5 L4 and sleeping for 10000 seconds
    job_configs = []
    job_names = []
    for i in range(6):
        job_name = f'{name}-job-{i+1}'
        job_names.append(job_name)
        job_config = textwrap.dedent(f"""
        name: {job_name}
        resources:
            accelerators: {{L4:0.5}}
        run: |
            sleep 10000
        """)
        job_configs.append(job_config)

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_1, \
             tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_2, \
             tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_3, \
             tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_4, \
             tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_5, \
             tempfile.NamedTemporaryFile(delete=True, suffix='.yaml') as job_yaml_6:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml_1, job_configs[0])
            write_yaml(job_yaml_2, job_configs[1])
            write_yaml(job_yaml_3, job_configs[2])
            write_yaml(job_yaml_4, job_configs[3])
            write_yaml(job_yaml_5, job_configs[4])
            write_yaml(job_yaml_6, job_configs[5])

            test = smoke_tests_utils.Test(
                'test_pool_fractional_gpu_scheduling',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    # Launch all 6 jobs
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_1.name),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_2.name),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_3.name),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_4.name),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_5.name),
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml_6.name),
                    # Verify all 6 jobs reach RUNNING status
                    check_num_running_jobs(
                        job_names, expected_count=6, timeout=timeout),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_one_job_per_worker_no_resources(generic_cloud: str):
    """Test that when no resources are specified, only 1 job runs per worker.

    This test validates that jobs without resource specifications are
    limited to 1 job per worker. The test:
    1. Launches a pool with 1 worker
    2. Launches 1 job that runs forever
    3. Waits for it to be RUNNING
    4. Launches another job that runs forever
    5. Verifies the second job stays PENDING (doesn't run)
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)

    job_name_1 = f'{name}-job-1'
    job_name_2 = f'{name}-job-2'

    # Create jobs with no resources specified (should default to 1 job per worker)
    job_config_1 = basic_job_conf(
        job_name=job_name_1,
        run_cmd='sleep infinity',
    )
    job_config_2 = basic_job_conf(
        job_name=job_name_2,
        run_cmd='sleep infinity',
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml, \
         tempfile.NamedTemporaryFile(delete=True) as job_yaml_1, \
         tempfile.NamedTemporaryFile(delete=True) as job_yaml_2:
        write_yaml(pool_yaml, pool_config)
        write_yaml(job_yaml_1, job_config_1)
        write_yaml(job_yaml_2, job_config_2)

        test = smoke_tests_utils.Test(
            'test_pool_one_job_per_worker_no_resources',
            [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
                # Launch first job
                _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                    pool_name=pool_name,
                    job_yaml=job_yaml_1.name,
                    job_name=job_name_1),
                # Wait for first job to be RUNNING
                wait_until_job_status(job_name_1, ['RUNNING'], timeout=timeout),
                # Launch second job
                _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                    pool_name=pool_name,
                    job_yaml=job_yaml_2.name,
                    job_name=job_name_2),
                # Wait for second job to be PENDING
                wait_until_job_status(job_name_2, ['PENDING'],
                                      bad_statuses=['RUNNING'],
                                      timeout=timeout),
                # Sleep for 30 seconds
                'sleep 30',
                # Verify second job is still PENDING (not RUNNING)
                wait_until_job_status(job_name_2, ['PENDING'],
                                      bad_statuses=['RUNNING'],
                                      timeout=30),
            ],
            timeout=timeout,
            teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
        )

        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_secrets_preserved_on_worker_update(generic_cloud: str):
    """Test that secrets provided via CLI are preserved when updating pool workers.

    This test:
    1. Creates a pool with a secret defined as null in YAML but set via CLI
    2. Verifies the secret is accessible in setup commands on worker 1
    3. Updates worker count to 2
    4. Verifies the secret is accessible in setup commands on worker 2
    """

    def check_for_secret_in_worker_logs(pool_name: str, worker_id: int,
                                        secret_value: str):
        """Check that worker logs contain the expected secret value."""
        return (
            f's=$(sky jobs pool logs {pool_name} {worker_id} --no-follow 2>&1); '
            f'echo "$s"; '
            f'if ! echo "$s" | grep "{secret_value}"; then '
            f'  echo "ERROR: Worker {worker_id} logs do not contain expected secret value: {secret_value}"; '
            f'  exit 1; '
            f'fi; '
            f'echo "Worker {worker_id} logs contain expected secret value: {secret_value}"'
        )

    secret_value = 'test-secret-value-12345'
    secret_name = 'FAKE_GITLAB_TOKEN'

    # Create pool config with secret as null in YAML
    pool_config = textwrap.dedent(f"""
    secrets:
        {secret_name}: null

    pool:
        workers: 1

    resources:
        infra: {generic_cloud}

    setup: |
        echo "Secret value: ${{{secret_name}}}"
        echo "Setup complete"
    """)

    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        write_yaml(pool_yaml, pool_config)
        pool_name = f'{smoke_tests_utils.get_cluster_name()}-pool'

        test = smoke_tests_utils.Test(
            'test_pool_secrets_preserved_on_worker_update',
            [
                # Launch pool with secret provided via CLI
                f's=$(sky jobs pool apply -p {pool_name} {pool_yaml.name} --secret {secret_name}={secret_value} -y); '
                f'echo "$s"; '
                f'echo; echo; echo "$s" | grep "Successfully created pool"',
                # Wait for pool to be ready
                wait_until_pool_ready(pool_name, timeout=timeout),
                # Wait for worker 1 to be ready
                wait_until_worker_status(
                    pool_name, 'READY', timeout=timeout, num_occurrences=1),
                # Check that worker 1 logs contain the secret value
                check_for_secret_in_worker_logs(pool_name, 1, secret_value),
                # Update workers to 2
                _POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS.format(
                    pool_name=pool_name, num_workers=2),
                # Wait for both workers to be ready
                wait_until_num_workers(pool_name, 2, timeout=timeout),
                wait_until_worker_status(
                    pool_name, 'READY', timeout=timeout, num_occurrences=2),
                # Check that worker 2 logs also contain the secret value
                check_for_secret_in_worker_logs(pool_name, 2, secret_value),
            ],
            timeout=timeout,
            teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
        )

        smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_num_jobs_rank(generic_cloud: str):
    """Test that SKYPILOT_JOB_RANK is correctly set for jobs launched with --num-jobs.

    Launches 3 jobs with --num-jobs 3, waits for each to succeed, and verifies
    that each job's logs show the correct rank (which should be job_id - 1).
    """
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    job_config = basic_job_conf(job_name=f'{name}-job',
                                run_cmd='echo "My rank is $SKYPILOT_JOB_RANK"')
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    NUM_JOBS = 3

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            # Build test commands
            test_commands = [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
            ]

            launch_cmd = (
                's=$(sky jobs launch --pool {pool_name} {job_yaml} --num-jobs {NUM_JOBS} -d -y); '
                'echo "$s"; '
                'echo "$s" | grep "Jobs submitted with IDs:" | sed "s/.*IDs: \\([0-9,]*\\).*/\\1/" > /tmp/job_ids.txt; '
                'cat /tmp/job_ids.txt').format(pool_name=pool_name,
                                               job_yaml=job_yaml.name,
                                               NUM_JOBS=NUM_JOBS)
            test_commands.append(launch_cmd)

            START_JOB_ID = 1
            job_ids = [i for i in range(START_JOB_ID, START_JOB_ID + NUM_JOBS)]
            for job_id in job_ids:
                test_commands.append(
                    wait_until_job_status_by_id(
                        job_id, ['SUCCEEDED'],
                        ['CANCELLED', 'FAILED_CONTROLLER'],
                        timeout=timeout))

            # Wait for the job logs to be ready.
            test_commands.append('sleep 30')

            for job_id in job_ids:
                test_commands.append(
                    check_logs(job_id, f'My rank is {job_id - 1}'))

            test = smoke_tests_utils.Test(
                'test_pools_num_jobs_rank',
                test_commands,
                timeout=timeout * 2,  # Give extra time for multiple jobs
                teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pools_num_jobs_speed(generic_cloud: str):
    """Test that we can launch a large number of jobs quickly.
    """
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'
    pool_config = basic_pool_conf(num_workers=1, infra=generic_cloud)
    job_config = basic_job_conf(job_name=f'{name}-job',
                                run_cmd='echo "My rank is $SKYPILOT_JOB_RANK"')
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    NUM_JOBS = 10

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            # Build test commands
            test_commands = [
                _LAUNCH_POOL_AND_CHECK_SUCCESS.format(pool_name=pool_name,
                                                      pool_yaml=pool_yaml.name),
                wait_until_pool_ready(pool_name, timeout=timeout),
            ]
            launch_timeout = 70
            launch_cmd = (
                'timeout {launch_timeout} bash -c "sky jobs launch --pool {pool_name} {job_yaml} --num-jobs {NUM_JOBS} -d -y"'
            ).format(pool_name=pool_name,
                     job_yaml=job_yaml.name,
                     NUM_JOBS=NUM_JOBS,
                     launch_timeout=launch_timeout)
            test_commands.append(launch_cmd)

            test = smoke_tests_utils.Test(
                'test_pools_num_jobs_speed',
                test_commands,
                timeout=timeout * 2,  # Give extra time for multiple jobs
                # Try to tear down multiple times since jobs may take a while
                # to get to pending.
                teardown=_TEARDOWN_POOL.format(pool_name=pool_name),
            )
            smoke_tests_utils.run_one_test(test)


def autoscaling_pool_conf(
    num_workers: int,
    max_workers: int,
    min_workers: Optional[int] = None,
    queue_length_threshold: Optional[int] = None,
    upscale_delay_seconds: Optional[int] = None,
    downscale_delay_seconds: Optional[int] = None,
    infra: str = 'aws',
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    setup_cmd: str = 'echo "setup message"',
):
    """Create a pool config with autoscaling enabled.
    
    Args:
        num_workers: Initial number of workers (also used as min if min_workers not set)
        max_workers: Maximum number of workers for autoscaling
        min_workers: Minimum number of workers for autoscaling (defaults to num_workers)
        queue_length_threshold: Queue length threshold for autoscaling (defaults to 1)
        upscale_delay_seconds: Delay before scaling up (defaults to None)
        downscale_delay_seconds: Delay before scaling down (defaults to None)
        infra: Infrastructure provider
        cpus: CPU requirements
        memory: Memory requirements
        setup_cmd: Setup command
    """
    cpus_string = f'    cpus: {cpus}\n' if cpus else ''
    memory_string = f'    memory: {memory}\n' if memory else ''
    min_workers_str = f'        min_workers: {min_workers}\n' if min_workers is not None else ''
    queue_threshold_str = f'        queue_length_threshold: {queue_length_threshold}\n' if queue_length_threshold is not None else ''
    upscale_delay_str = f'        upscale_delay_seconds: {upscale_delay_seconds}\n' if upscale_delay_seconds is not None else ''
    downscale_delay_str = f'        downscale_delay_seconds: {downscale_delay_seconds}\n' if downscale_delay_seconds is not None else ''
    return textwrap.dedent(f"""
    pool:
        workers: {num_workers}
        max_workers: {max_workers}
{min_workers_str}{queue_threshold_str}{upscale_delay_str}{downscale_delay_str}
    resources:
        infra: {infra}
{cpus_string}{memory_string}
    setup: |
        {setup_cmd}
    """)


def check_workers_do_not_exceed(pool_name: str,
                                max_workers: int,
                                duration: int = 60,
                                time_between_checks: int = 10):
    """Check that workers never exceed max_workers for a given duration."""
    num_checks = duration // time_between_checks
    return (
        f'for i in {{1..{num_checks}}}; do '
        f's=$(sky jobs pool status {pool_name} | grep "^{pool_name}"); '
        'echo "$s"; '
        f'current=$(echo "$s" | grep -oE "[0-9]+/[0-9]+" | head -1 | cut -d"/" -f1); '
        f'if [ -n "$current" ] && [ "$current" -gt {max_workers} ]; then '
        f'  echo "ERROR: Workers ($current) exceeded max_workers ({max_workers})"; '
        '  exit 1; '
        'fi; '
        f'echo "Check $i/{num_checks}: Workers = $current (max = {max_workers})"; '
        f'sleep {time_between_checks}; '
        'done; '
        'echo "Workers did not exceed max_workers during the check period"')


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_autoscaling_scale_up(generic_cloud: str):
    """Test that pool autoscales up when jobs are queued.
    
    This test:
    1. Creates a pool with workers=1, max_workers=3 (2 higher than initial)
    2. Launches multiple jobs that will queue up
    3. Verifies that workers scale up to max_workers
    4. Verifies at least 2 scaling events occur (1->2, 2->3)
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with autoscaling: start with 1 worker, can scale up to 3
    pool_config = autoscaling_pool_conf(
        num_workers=1,
        max_workers=3,
        infra=generic_cloud,
        setup_cmd='echo hi',
        upscale_delay_seconds=20,
        downscale_delay_seconds=20,
    )

    # Job that runs for a while to keep queue length high
    job_name = f'{name}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='sleep 3000',  # Long-running job
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            test = smoke_tests_utils.Test(
                'test_pool_autoscaling_scale_up',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    # Launch multiple jobs to create queue
                    # Launch 5 jobs - first one runs, others queue up
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}') for i in range(1, 6)
                    ],
                    # Verify we scale up to 3 workers (second scaling event)
                    wait_until_num_workers(pool_name, 3, timeout=300),
                    # Verify we stay at 3 workers (max_workers)
                    'sleep 30',
                    wait_until_num_workers(pool_name, 3, timeout=30),
                ],
                timeout=timeout * 2,  # Autoscaling takes time
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=10),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_autoscaling_no_scale_when_max_equals_workers(generic_cloud: str):
    """Test that pool does not scale above workers when max_workers == workers.
    
    This test:
    1. Creates a pool with workers=2, max_workers=2 (same as workers)
    2. Launches multiple jobs that will queue up
    3. Verifies that workers never exceed 2 even with jobs queued
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with autoscaling: start with 2 workers, max_workers=2 (no scaling up)
    pool_config = autoscaling_pool_conf(
        num_workers=2,
        max_workers=2,
        infra=generic_cloud,
        setup_cmd='echo hi',
        upscale_delay_seconds=20,
        downscale_delay_seconds=20,
    )

    # Job that runs for a while
    job_name = f'{name}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='sleep 3000',  # Long-running job
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            test = smoke_tests_utils.Test(
                'test_pool_autoscaling_no_scale_when_max_equals_workers',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(pool_name, timeout=timeout),
                    # Launch multiple jobs to create queue (more than workers can handle)
                    # Launch 5 jobs - 2 run, 3 queue up
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}') for i in range(1, 6)
                    ],
                    # Verify we start with 2 workers
                    wait_until_num_workers(pool_name, 2, timeout=timeout),
                    # Wait for jobs to queue
                    'sleep 10',
                    # Verify jobs are queued
                    f's=$(sky jobs queue); echo "$s"; echo "$s" | grep "PENDING" || echo "Some jobs may have started"',
                    # Check that workers never exceed 2 for a period of time
                    check_workers_do_not_exceed(pool_name, 2, duration=120),
                ],
                timeout=timeout * 2,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=10),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_autoscaling_scale_down_to_zero(generic_cloud: str):
    """Test that pool autoscales down to zero when no jobs and min_workers=0.
    
    This test:
    1. Creates a pool with workers=1, max_workers=2, min_workers=0
    2. Launches a job that completes quickly
    3. Verifies that workers scale down to 0 after job completes
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with autoscaling: start with 1 worker, can scale down to 0
    pool_config = autoscaling_pool_conf(
        num_workers=1,
        max_workers=2,
        min_workers=0,
        infra=generic_cloud,
        upscale_delay_seconds=20,
        downscale_delay_seconds=20,
    )

    # Job that completes quickly
    job_name = f'{name}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo "Job completed"',  # Quick job
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            test = smoke_tests_utils.Test(
                'test_pool_autoscaling_scale_down_to_zero',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Launch a job
                    _LAUNCH_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    # Wait for job to complete
                    wait_until_job_status(job_name, ['SUCCEEDED'],
                                          timeout=timeout),
                    # Verify we scale down to 0 workers
                    wait_until_num_workers(pool_name, 0, timeout=timeout),
                ],
                timeout=timeout * 2,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=10),
            )
            smoke_tests_utils.run_one_test(test)


@pytest.mark.no_remote_server  # see note 1 above
def test_pool_autoscaling_scale_up_to_max_then_down_to_zero(generic_cloud: str):
    """Test that pool autoscales up to max_workers then down to zero.
    
    This test:
    1. Creates a pool with workers=0, max_workers=3, min_workers=0
    2. Queues up enough quick jobs (echo hi) to trigger scaling to 3 workers
    3. Verifies that workers scale up to 3
    4. Waits for all jobs to complete
    5. Waits for SCALE_DOWN_TO_ZERO message in pool logs
    6. Verifies that workers scale down to 0
    """
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    name = smoke_tests_utils.get_cluster_name()
    pool_name = f'{name}-pool'

    # Pool with autoscaling: start with 0 workers, can scale up to 3, down to 0
    pool_config = autoscaling_pool_conf(
        num_workers=1,
        max_workers=3,
        min_workers=0,
        infra=generic_cloud,
        setup_cmd='echo hi',
        upscale_delay_seconds=20,
        downscale_delay_seconds=20,
    )

    # Quick job that just echoes hi and finishes instantly
    job_name = f'{name}-job'
    job_config = basic_job_conf(
        job_name=job_name,
        run_cmd='echo hi',  # Quick job that finishes instantly
    )

    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)

            test = smoke_tests_utils.Test(
                'test_pool_autoscaling_scale_up_to_max_then_down_to_zero',
                [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    # Queue up enough jobs to trigger scaling to 3 workers
                    # We need at least 3 jobs queued to trigger scaling to 3
                    # Launch 5 jobs to ensure we get to 3 workers
                    *[
                        _LAUNCH_JOB_AND_CHECK_SUCCESS_WITH_NAME.format(
                            pool_name=pool_name,
                            job_yaml=job_yaml.name,
                            job_name=f'{job_name}-{i}') for i in range(1, 6)
                    ],
                    # Verify we scale up to 3 workers (max_workers)
                    wait_until_num_workers(pool_name, 3, timeout=300),
                    # Wait for all jobs to complete
                    *[
                        wait_until_job_status(f'{job_name}-{i}', ['SUCCEEDED'],
                                              timeout=timeout)
                        for i in range(1, 6)
                    ],
                    # Wait for SCALE_DOWN_TO_ZERO message in pool logs
                    wait_for_message_in_pool_logs(
                        pool_name, 'SCALE_DOWN_TO_ZERO', timeout=300),
                    # Verify we scale down to 0 workers
                    wait_until_num_workers(pool_name, 0, timeout=300),
                ],
                timeout=timeout * 3,  # Autoscaling takes time
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=10),
            )
            smoke_tests_utils.run_one_test(test)
