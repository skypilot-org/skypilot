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
            (f'ENDPOINT=$(sky status --ip {name}); '
             f's=$(curl -sS http://$ENDPOINT:8000/v1/chat/completions -H "Content-Type: application/json" -d \'{json_payload}\'); '
             f'echo "$s" | jq .; '
             f'content=$(echo "$s" | jq -r ".choices[0].message.content"); '
             f'echo "$content"; '
             f'echo "$content" | grep "<think>"'),
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_kubernetes  # GPU not available in k8s test cluster
def test_sglang_llama2_serving(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    payload = {
        "model": "meta-llama/Llama-2-7b-chat-hf",
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
        'sglang_llama2',
        [
            f'sky serve up -n {name} --infra {generic_cloud} --gpus L4:1 -y llm/sglang/llama2.yaml',
            SERVE_WAIT_UNTIL_READY.format(name=name, replica_num=2),
            (f'{SERVE_ENDPOINT_WAIT.format(name=name)}; '
             f's=$(curl -sS $endpoint/v1/chat/completions -H "Content-Type: application/json" -d \'{json_payload}\'); '
             f'echo "$s" | jq .; '
             f'echo "$s" | jq -r ".choices[0].message.content" | grep -i "assistant"'
            ),
        ],
        TEARDOWN_SERVICE.format(name=name),
        env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        timeout=40 * 60,
    )
    smoke_tests_utils.run_one_test(test)
