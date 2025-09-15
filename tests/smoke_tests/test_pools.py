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


def test_vllm_pool(generic_cloud: str):
    pool_config = textwrap.dedent(f"""
    envs:
        MODEL_NAME: NousResearch/Meta-Llama-3-8B-Instruct

    resources:
        accelerators: {{L4}}

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

    bucket_name = f'sky-test-vllm-pool-{generic_cloud}'

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
            pool_yaml.write(pool_config.encode())
            pool_yaml.flush()
            job_yaml.write(job_config.encode())
            job_yaml.flush()

            name = smoke_tests_utils.get_cluster_name()
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
                teardown=f'sky jobs pool down {pool_name} -y',
            )

            smoke_tests_utils.run_one_test(test)
