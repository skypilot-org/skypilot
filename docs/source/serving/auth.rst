.. _serve-auth:

Authorization
=============

SkyServe provides robust authorization capabilities at the replica level, allowing you to control access to service endpoints with API keys.

Setup API Keys
------------

SkyServe relies on the authorization of the service running on underlying service replicas, e.g., the inference engine. We take an example vLLM inference engine, which supports static API key authorization with an argument :code`--api-key`.

We first define a normal SkyPilot job with an LLM service setup with vLLM and an API key set:

.. code-block:: yaml
    :emphasize-lines: 26

    envs:
      MODEL_NAME: meta-llama/Llama-2-7b-chat-hf
      HF_TOKEN: # TODO: Fill with your own huggingface token, or use --env to pass.
      AUTH_TOKEN: # TODO: Fill with your own auth token (a random string), or use --env to pass.

    resources:
      accelerators: {L4:1, A10G:1, A10:1, A100:1, A100-80GB:1}
      ports: 8000

    setup: |
      pip install transformers==4.38.0
      pip install vllm==0.3.2
      python -c "import huggingface_hub; huggingface_hub.login('${HF_TOKEN}')"

    run: |
      echo 'Starting vllm openai api server...'
      python -m vllm.entrypoints.openai.api_server \
        --model $MODEL_NAME --tokenizer hf-internal-testing/llama-tokenizer \
        --host 0.0.0.0 --port 8000 \
        --api-key $AUTH_TOKEN

SkyServe's proxy design ensures that all headers in the original request, including the authorization token, are forwarded to the vLLM inference engine. The engine then validates the token.

To enable this feature in SkyServe, you need to configure the readiness probe to include the access token. This ensures the readiness probe passes the authorization check. Here's how you set it up in the :code:`service.readiness_probe` section:

.. code-block:: yaml
    :emphasize-lines: 4-5

    service:
      readiness_probe:
        path: /v1/models
        headers:
          Authorization: Bearer $AUTH_TOKEN
      replicas: 1

Notice that we will automatically replace the :code:`$AUTH_TOKEN` in the service section with the actual token value as well.
