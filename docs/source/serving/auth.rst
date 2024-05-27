.. _serve-auth:

Authorization
=============

SkyServe provides robust authorization capabilities at the replica level, allowing you to control access to service endpoints with API keys.

Setup API Keys
--------------

SkyServe relies on the authorization of the service running on underlying service replicas, e.g., the inference engine. We take the vLLM inference engine as an example, which supports static API key authorization with an argument :code`--api-key`.

We define a SkyServe service spec for serving Llama-3 chatbot with vLLM and an API key. In the example YAML below, we define the authorization token as an environment variable, :code:`AUTH_TOKEN`, and pass it to both the service field to enable readiness_probe to access the replicas and the vllm entrypoint to start services on replicas with the API key.

.. code-block:: yaml
  :emphasize-lines: 5-6,12,28

  # auth.yaml
  service:
    readiness_probe:
      path: /v1/models
      headers:
        Authorization: Bearer $AUTH_TOKEN
    replicas: 2

  envs:
    MODEL_NAME: meta-llama/Meta-Llama-3-8B-Instruct
    HF_TOKEN: # TODO: Fill with your own huggingface token, or use --env to pass.
    AUTH_TOKEN: # TODO: Fill with your own auth token (a random string), or use --env to pass.

  resources:
    accelerators: {L4, A10g, A10, L40, A40, A100, A100-80GB}
    ports: 8000

  setup: |
    pip install vllm==0.4.0.post1 flash-attn==2.5.7 gradio openai
    # python -c "import huggingface_hub; huggingface_hub.login('${HF_TOKEN}')"

  run: |
    export PATH=$PATH:/sbin
    python -m vllm.entrypoints.openai.api_server \
      --model $MODEL_NAME --trust-remote-code \
      --gpu-memory-utilization 0.95 \
      --host 0.0.0.0 --port 8000 \
      --api-key $AUTH_TOKEN

To deploy the service, run the following command:

.. code-block:: bash

  HF_TOKEN=xxx AUTH_TOKEN=yyy sky serve up auth.yaml -n auth --env HF_TOKEN --env AUTH_TOKEN

To send a request to the service endpoint, a service client need to include the static API key in a request's header:

.. code-block:: bash

  $ ENDPOINT=$(sky serve status --endpoint auth)
  $ AUTH_TOKEN=yyy
  $ curl http://$ENDPOINT/v1/chat/completions \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $AUTH_TOKEN" \
      -d '{
        "model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "messages": [
          {
            "role": "system",
            "content": "You are a helpful assistant."
          },
          {
            "role": "user",
            "content": "Who are you?"
          }
        ],
        "stop_token_ids": [128009, 128001]
      }'
  {"id":"cmpl-0e93e6ea6e9c40f5805135afec75f163","object":"chat.completion","created":1716714050,"model":"meta-llama/Meta-Llama-3-8B-Instruct","choices":[{"index":0,"message":{"role":"assistant","content":"I'm your friendly AI assistant! I'm a computer program designed to help you with a wide range of tasks and answer your questions to the best of my ability. I'm here to provide information, offer suggestions, and assist you in any way I can. I'm constantly learning and improving, so the more you interact with me, the better I'll become at understanding your needs and providing helpful responses.\n\nI can help with things like:\n\n* Answering general knowledge questions\n* Providing definitions for words and phrases\n* Giving advice on topics like science, history, and technology\n* Assisting with language-related tasks, such as grammar and proofreading\n* Generating text and writing articles or stories\n* Even having fun conversations and playing games with you!\n\nSo, what can I help you with today?"},"logprobs":null,"finish_reason":"stop","stop_reason":128009}],"usage":{"prompt_tokens":26,"total_tokens":188,"completion_tokens":162}}

If the Authorization header is missing or invalid, the service will return a 401 Unauthorized error:

.. code-block:: bash

  $ curl http://$ENDPOINT/v1/models
  {"error": "Unauthorized"}

  $ curl http://$ENDPOINT/v1/models -H "Authorization: Bearer random-string"
  {"error": "Unauthorized"}
