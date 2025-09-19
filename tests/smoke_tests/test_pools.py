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

_LAUNCH_POOL_JOB_AND_CHECK_SUCCESS = (
    's=$(sky jobs launch --pool {pool_name} {job_yaml} -y); '
    'echo "$s"; '
    'echo; echo; echo "$s" | grep "Job finished (status: SUCCEEDED)."')

_TEARDOWN_POOL = ('sky jobs pool down {pool_name} -y')

_CANCEL_POOL_JOBS = ('sky jobs pool cancel {pool_name} -y')

_DELETE_BUCKET = ('sky storage delete -y {bucket_name}')


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


def check_for_setup_message(pool_name: str,
                            setup_message: str,
                            follow: bool = True):
    return (
        f's=$(sky jobs pool logs {pool_name} 1 {"--no-follow" if not follow else ""}); '
        f'echo "$s"; echo; echo; echo "$s" | grep "{setup_message}"')


def basic_pool_conf(num_workers: int, setup_cmd: str = 'echo "setup message"'):
    return textwrap.dedent(f"""
    pool:
        workers: {num_workers}

    resources:
        cpus: 2+
        memory: 4GB+

    setup: |
        {setup_cmd}
    """)


def write_yaml(yaml_file: tempfile.NamedTemporaryFile, config: str):
    yaml_file.write(config.encode())
    yaml_file.flush()


def test_vllm_pool(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    bucket_name = f'sky-test-vllm-pool-{name}'
    pool_config = textwrap.dedent(f"""
    envs:
        MODEL_NAME: NousResearch/Meta-Llama-3-8B-Instruct
       
        BUCKET_NAME: {bucket_name}

    file_mounts:
        /output:
            name: ${{BUCKET_NAME}}
            mode: MOUNT
            store: s3

    resources:
        accelerators: {{L4: 1}}

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

    job_config = textwrap.dedent(f"""
    name: t-test-vllm-pool

    resources:
        cpus: 4
        accelerators:
            L4: 1
        any_of:
            - use_spot: true
            - use_spot: false

    envs:
        START_IDX: 0  # Will be overridden by batch launcher script
        END_IDX: 10000  # Will be overridden by batch launcher script
        MODEL_NAME: "Alibaba-NLP/gte-Qwen2-7B-instruct"
        DATASET_NAME: "McAuley-Lab/Amazon-Reviews-2023"
        DATASET_CONFIG: "raw_review_Books"
        WORKER_ID: ''

    run: |
        source .venv/bin/activate

        # Initialize and download the model
        HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download --local-dir /tmp/model $MODEL_NAME

        # Set worker ID for metrics tracking
        if [ -z "$WORKER_ID" ]; then
            export WORKER_ID="worker_$(date +%s)_$(hostname)"
            echo "Generated worker ID: $WORKER_ID"
        fi

        output_dir="/output/${{WORKER_ID}}"
        mkdir -p $output_dir

        # Create metrics directory
        mkdir -p $output_dir/metrics


        # Process the assigned range of documents
        echo "Processing documents from $START_IDX to $END_IDX"

        # Process text documents and track token metrics
        python scripts/text_vector_processor.py \
            --output-path "${{output_dir}}/embeddings_${{START_IDX}}_${{END_IDX}}.parquet" \
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
                'test_vllm_pool', [
                    _LAUNCH_POOL_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, pool_yaml=pool_yaml.name),
                    wait_until_pool_ready(
                        pool_name,
                        timeout=smoke_tests_utils.get_timeout(generic_cloud)),
                    _LAUNCH_POOL_JOB_AND_CHECK_SUCCESS.format(
                        pool_name=pool_name, job_yaml=job_yaml.name),
                    f'aws s3 ls s3://{bucket_name} --recursive --summarize | grep "Total Objects" | awk \'{{print $3}}\'',
                ],
                timeout=smoke_tests_utils.get_timeout(generic_cloud),
                teardown=
                f'{_TEARDOWN_POOL.format(pool_name=pool_name)} && {_DELETE_BUCKET.format(bucket_name=bucket_name)}'
            )

            smoke_tests_utils.run_one_test(test)


def test_setup_logs_in_starting_pool(generic_cloud: str):
    """Test that setup logs are streamed in starting state."""
    # Do a very long setup so we know the setup logs are streamed in
    pool_config = basic_pool_conf(
        1, 'for i in {1..10000}; do echo "Noisy setup $i"; sleep 1; done')
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
    pool_config = basic_pool_conf(num_workers=1)
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
