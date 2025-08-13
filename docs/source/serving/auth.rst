.. _serve-auth:

Authorization
=============

SkyServe provides robust authorization capabilities at the replica level, allowing you to control access to service endpoints with API keys.

Setup API keys
--------------

SkyServe relies on the authorization of the service running on underlying service replicas, e.g., the inference engine. We take the vLLM inference engine as an example, which supports static API key authorization with an argument :code:`--api-key`.

We define a SkyServe service spec for serving Llama-3 chatbot with vLLM and an API key. In the example YAML below, we define the authorization token as an environment variable, :code:`AUTH_TOKEN`, and pass it to both the service field to enable :code:`readiness_probe` to access the replicas and the vllm entrypoint to start services on replicas with the API key.

.. code-block:: yaml
  :emphasize-lines: 5,10-11,28

  # auth.yaml
  envs:
    MODEL_NAME: Qwen/Qwen3-0.6B

  secrets:
    HF_TOKEN: null
    AUTH_TOKEN: null

  service:
    readiness_probe:
      path: /v1/models
      headers:
        Authorization: Bearer $AUTH_TOKEN
      initial_delay_seconds: 1800
    replicas: 1

  resources:
    accelerators: {L4, A10g, A10, L40, A40, A100, A100-80GB}
    cpus: 7+
    memory: 20+
    ports: 8087

  setup: |
    uv venv --python 3.10 --seed
    source .venv/bin/activate
    uv pip install vllm==0.10.0 --torch-backend=auto
    # Have to use triton==3.2.0 to avoid https://github.com/triton-lang/triton/issues/6698
    uv pip install triton==3.2.0
    uv pip install openai

  run: |
    source .venv/bin/activate
    export PATH=$PATH:/sbin
    vllm serve $MODEL_NAME --trust-remote-code \
      --host 0.0.0.0 --port 8087 \
      --api-key $AUTH_TOKEN


To deploy the service, run the following command:

.. code-block:: bash

  HF_TOKEN=xxx AUTH_TOKEN=yyy sky serve up auth.yaml -n auth --secret HF_TOKEN --secret AUTH_TOKEN

To send a request to the service endpoint, a service client need to include the static API key in a request's header:

.. code-block:: bash
  :emphasize-lines: 5

  $ ENDPOINT=$(sky serve status --endpoint auth)
  $ AUTH_TOKEN=yyy
  $ curl $ENDPOINT/v1/chat/completions \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $AUTH_TOKEN" \
      -d '{
        "model": "Qwen/Qwen3-0.6B",
        "messages": [
          {
            "role": "system",
            "content": "You are a helpful assistant."
          },
          {
            "role": "user",
            "content": "Who are you?"
          }
        ]
      }' | jq

.. raw:: HTML

  <details>

  <summary>Example output</summary>


.. code-block:: console

  {
  "id": "chatcmpl-f5f1bffa4b504a8b8e842436f3701b3f",
  "object": "chat.completion",
  "created": 1753994285,
  "model": "Qwen/Qwen3-0.6B",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\nOkay, the user is asking, \"Who are you?\" I need to respond appropriately. First, I should acknowledge their question and explain that I'm an AI assistant. I should mention that I'm designed to help with various tasks and provide information. I should keep it friendly and open-ended to encourage further interaction. Let me make sure the response is clear and concise.\n</think>\n\nI'm an AI assistant designed to help with a wide range of questions and tasks. How can I assist you today? 😊",
        "refusal": null,
        "annotations": null,
        "audio": null,
        "function_call": null,
        "tool_calls": [],
        "reasoning_content": null
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "service_tier": null,
  "system_fingerprint": null,
  "usage": {
    "prompt_tokens": 23,
    "total_tokens": 128,
    "completion_tokens": 105,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null,
  "kv_transfer_params": null
  }

.. raw:: html

  </details>

A service client without an API key will not be able to access the service and get a :code:`401 Unauthorized` error:

.. code-block:: bash

  $ curl $ENDPOINT/v1/models
  {"error": "Unauthorized"}

  $ curl $ENDPOINT/v1/models -H "Authorization: Bearer random-string"
  {"error": "Unauthorized"}
