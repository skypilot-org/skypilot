# SkyServe Example for vLLM + Authentication

This example demonstrates how to use SkyServe with vLLM and authentication. [The example](./task.yaml) is a modification for the [vLLM example](../../../llm/vllm/service.yaml) to include authentication. The only modification is wrapped between `# === AUTH START ===` and `# ===  AUTH END  ===`.

Usage:

```bash
# Spin up the service
$ sky serve up examples/serve/authentication/task.yaml -n vllm-auth
```

The `/v1/models` endpoint is not protected by authentication, so you can access it without a token, just like a normal SkyServe service:

```bash
$ curl -L http://$(sky serve status --endpoint vllm-auth)/v1/models
{"object":"list","data":[{"id":"meta-llama/Llama-2-7b-chat-hf","object":"model","created":1710926036,"owned_by":"vllm","root":"meta-llama/Llama-2-7b-chat-hf","parent":null,"permission":[{"id":"modelperm-7f7decd2ccac4e75969c91200455b7f9","object":"model_permission","created":1710926036,"allow_create_engine":false,"allow_sampling":true,"allow_logprobs":true,"allow_search_indices":false,"allow_view":true,"allow_fine_tuning":false,"organization":"*","group":null,"is_blocking":false}]}]}
```

The endpoints that will incur computation costs are protected by authentication. If not access with a valid token, the service will return an error:

```bash
$ curl -L http://$(sky serve status --endpoint vllm-auth)/v1/chat/completions \
    -X POST \
    -H 'Content-Type: application/json' \
    -d '{"model": "meta-llama/Llama-2-7b-chat-hf", "messages": [{"role": "user", "content": "Who are you?"}]}'
Invalid authentication credentials
```

You can access them by providing a valid token:

```bash
$ curl --location-trusted http://$(sky serve status --endpoint vllm-auth)/v1/chat/completions \
    -H "Authorization: Bearer static_secret_token" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d '{"model": "meta-llama/Llama-2-7b-chat-hf", "messages": [{"role": "user", "content": "Who are you?"}]}'
{"id":"cmpl-8a6b0341d8644dbbb8b66bde112fbb70","object":"chat.completion","created":1983,"model":"meta-llama/Llama-2-7b-chat-hf","choices":[{"index":0,"message":{"role":"assistant","content":"  Hello! I'm LLaMA, an AI assistant developed by Meta AI that can understand and respond to human input in a conversational manner. Please let me know if there is anything specific you would like to talk about or ask me. I'm here to help!"},"finish_reason":"stop"}],"usage":{"prompt_tokens":13,"total_tokens":73,"completion_tokens":60}}
```

Notice that here we used the `--location-trusted` flag to allow the `curl` command to follow the redirect to the SkyServe service, while forward the authentication token to the redirected URL. The `-L` flag is not used here because it will not forward the authentication token to the redirected URL.
