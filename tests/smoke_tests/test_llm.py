# Smoke tests for SkyPilot for basic functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_llm.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_llm.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_llm.py::test_deepseek_r1
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_llm.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_llm.py --generic-cloud aws

import json

import pytest
from smoke_tests import smoke_tests_utils
# TODO(zeping): move them to smoke_tests_utils
from smoke_tests.test_sky_serve import SERVE_ENDPOINT_WAIT
from smoke_tests.test_sky_serve import SERVE_WAIT_UNTIL_READY
from smoke_tests.test_sky_serve import TEARDOWN_SERVICE

import sky


@pytest.mark.no_kubernetes  # Don't have GPU for k8s test cluster
@pytest.mark.parametrize('model_name,gpu_spec', [
    ('deepseek-ai/DeepSeek-R1-Distill-Llama-8B', 'L4:1'),
    ('deepseek-ai/DeepSeek-R1-Distill-Llama-70B', 'A100-80GB:1'),
])
def test_deepseek_r1_vllm(generic_cloud: str, model_name: str, gpu_spec: str):
    name = smoke_tests_utils.get_cluster_name()

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user",
                "content": "Who are you?"
            },
        ],
    }
    json_payload = json.dumps(payload)

    test = smoke_tests_utils.Test(
        'deepseek_r1_vllm',
        [
            f'sky launch -y -d -c {name} --infra {generic_cloud} --env MODEL_NAME={model_name} --gpus {gpu_spec} llm/deepseek-r1-distilled/deepseek-r1-vllm.yaml',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.UP],
                timeout=300),
            # Disable SKYPILOT_DEBUG while retrieving the IP to avoid debug logs
            # contaminating the output of `sky status --ip`, which would break curl.
            # Use `tail -n 1` to ensure only the pure IP/hostname is captured.
            (
                f'ORIGIN_SKYPILOT_DEBUG=$SKYPILOT_DEBUG; export SKYPILOT_DEBUG=0; '
                f'ENDPOINT=$(sky status --ip {name} | tail -n 1); '
                f'export SKYPILOT_DEBUG=$ORIGIN_SKYPILOT_DEBUG; '
                # Wait up to 10 minutes for the model server to be ready
                f'start_time=$SECONDS; timeout=600; s=""; '
                f'while true; do '
                f'  resp=$(curl -sS --max-time 15 http://$ENDPOINT:8000/v1/chat/completions '
                f'    -H "Content-Type: application/json" -d \'{json_payload}\' || true); '
                f'  if echo "$resp" | jq -e ".choices[0].message.content" > /dev/null 2>&1; then '
                f'    s="$resp"; break; fi; '
                f'  if (( SECONDS - start_time > timeout )); then '
                f'    echo "Timeout after $timeout seconds waiting for model server readiness"; echo "$resp"; exit 1; fi; '
                f'  echo "Waiting for model server to be ready..."; sleep 10; '
                f'done; '
                f'echo "$s" | jq .; '
                f'content=$(echo "$s" | jq -r ".choices[0].message.content"); '
                f'echo "$content"; '
                f'echo "$content" | grep "<think>"'),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_kubernetes  # Some GPUs not available in k8s test cluster
def test_sglang_llava_serving(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    payload = {
        "model": "liuhaotian/llava-v1.6-vicuna-7b",
        "messages": [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": "Describe this image"
            }, {
                "type": "image_url",
                "image_url": {
                    "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/examples/frontend_language/quick_start/images/cat.jpeg"
                }
            }]
        }],
    }
    json_payload = json.dumps(payload)

    test = smoke_tests_utils.Test(
        'sglang_llava',
        [
            f'sky serve up -n {name} --infra {generic_cloud} --gpus L4:1 -y llm/sglang/llava.yaml',
            SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            (f'{SERVE_ENDPOINT_WAIT.format(name=name)}; '
             f's=$(curl -sS $endpoint/v1/chat/completions -H "Content-Type: application/json" -d \'{json_payload}\'); '
             f'echo "$s" | jq .; '
             f'content=$(echo "$s" | jq -r ".choices[0].message.content"); '
             f'echo "$content"; '
             f'echo "$content" | grep -E ".+"'),
        ],
        TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=40 * 60,
    )
    smoke_tests_utils.run_one_test(test)
