import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import yaml_utils

_LAUNCH_POOL_AND_CHECK_SUCCESS = (
    's=$(sky jobs pool apply -p {pool_name} {pool_yaml} -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Successfully created pool"')

_LAUNCH_JOB_AND_CHECK_SUCCESS = (
    's=$(sky jobs launch --pool {pool_name} {job_yaml} -d -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Job submitted"; '
    'sleep 5')

_POOL_CHANGE_NUM_WORKERS_AND_CHECK_SUCCESS = (
    's=$(sky jobs pool apply -p {pool_name} --workers {num_workers} -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Successfully updated pool"')

_TEARDOWN_POOL = ('sky jobs pool down {pool_name} -y')

_CANCEL_POOL_JOBS = ('sky jobs cancel --pool {pool_name} -y')


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
                             time_between_checks: int = 5):
    status_str = f'sky jobs pool status {pool_name} | grep -A999 "^Pool Workers" | grep "^{pool_name}"'
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for worker status \'{status}\'"; exit 1; '
        'fi; '
        f's=$({status_str}); '
        'echo "$s"; '
        f'if echo "$s" | grep "{status}"; then '
        '  break; '
        'fi; '
        'if echo "$s" | grep "FAILED"; then '
        '  exit 1; '
        'fi; '
        'if echo "$s" | grep "SHUTTING_DOWN"; then '
        '  exit 1; '
        'fi; '
        f'echo "Waiting for worker status to be {status}..."; '
        f'sleep {time_between_checks}; '
        'done; '
        'sleep 1')


def wait_until_job_status(job_name: str,
                          status: str,
                          timeout: int = 30,
                          time_between_checks: int = 5):
    return (
        'start_time=$SECONDS; '
        'while true; do '
        f'if (( $SECONDS - $start_time > {timeout} )); then '
        f'  echo "Timeout after {timeout} seconds waiting for job to succeed"; exit 1; '
        'fi; '
        f's=$(sky jobs queue); '
        'echo "$s"; '
        f'if echo "$s" | grep "{job_name}" | grep "{status}"; then '
        '  break; '
        'fi; '
        f'if echo "$s" | grep "{job_name}" | grep "CANCELLED"; then '
        '  exit 1; '
        'fi; '
        f'if echo "$s" | grep "{job_name}" | grep "FAILED_CONTROLLER"; then '
        '  exit 1; '
        'fi; '
        'echo "Waiting for job to be running..."; '
        f'sleep {time_between_checks}; '
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


def basic_pool_conf(
    num_workers: int,
    infra: str,
    setup_cmd: str = 'echo "setup message"',
):
    return textwrap.dedent(f"""
    pool:
        workers: {num_workers}

    resources:
        cpus: 2+
        memory: 4GB+
        infra: {infra}

    setup: |
        {setup_cmd}
    """)


def basic_job_conf(
    job_name: str,
    run_cmd: str = 'echo "run message"',
):
    return textwrap.dedent(f"""
    name: {job_name}

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
def test_vllm_pool(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    pool_config = textwrap.dedent(f"""
    envs:
        MODEL_NAME: NousResearch/Meta-Llama-3-8B-Instruct

    resources:
        accelerators: {{L4}}
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
        cpus: 4
        accelerators:
            L4: 1
        any_of:
            - use_spot: true
            - use_spot: false
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
                    wait_until_job_status(job_name, 'PENDING', timeout=timeout),
                ],
                timeout=timeout,
                teardown=cancel_jobs_and_teardown_pool(pool_name, timeout=5),
            )

            smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
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
    timeout = smoke_tests_utils.get_timeout(generic_cloud)
    with tempfile.NamedTemporaryFile(delete=True) as pool_yaml:
        with tempfile.NamedTemporaryFile(delete=True) as job_yaml:
            write_yaml(pool_yaml, pool_config)
            write_yaml(job_yaml, job_config)
            get_instance_id_cmd = smoke_tests_utils.AWS_GET_INSTANCE_ID.format(
                region=region,
                name_on_cloud=get_worker_cluster_name(pool_name, 1))
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
                    wait_until_job_status(job_name, 'RUNNING', timeout=timeout),
                    # Restart the cluster manually.
                    smoke_tests_utils.run_cloud_cmd_on_cluster(
                        name,
                        cmd=(
                            f'id={get_instance_id_cmd} && '
                            f'echo "Instance ID: $id" && '
                            # Make sure the instance id is not empty.
                            f'[[ -z "$id" ]] && echo "Instance ID is empty" && exit 1 && '
                            f'aws ec2 stop-instances --region {region} '
                            f'--instance-ids $id && '
                            # Wait for the instance to be stopped before restarting.
                            f'aws ec2 wait instance-stopped --region {region} '
                            f'--instance-ids $id '),
                        skip_remote_server_check=True),
                    # # Wait until job is running.
                    wait_until_job_status(job_name, 'RUNNING', timeout=timeout),
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=
                f'{cancel_jobs_and_teardown_pool(pool_name, timeout=10)} && '
                f'{smoke_tests_utils.down_cluster_for_cloud_cmd(name, skip_remote_server_check=True)}',
            )

            smoke_tests_utils.run_one_test(test)
