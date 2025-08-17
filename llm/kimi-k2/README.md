
<!-- $REMOVE -->
# Run Kimi-K2 on Kubernetes or Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Kimi-K2 -->


[Kimi-K2](https://huggingface.co/moonshotai/Kimi-K2-Instruct) is a large language model developed by Moonshot AI. It features 32 billion activated parameters and 1 trillion total parameters, making it a powerful model that requires multi-node serving due to its substantial size.


## Prerequisites

- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.
- **Note**: This model requires at least 16 H100s due to its large size.

## Run Kimi-K2

```bash
HF_TOKEN=xxx sky launch kimi-k2.yaml -c kimi-k2 --secret HF_TOKEN
```

The `kimi-k2.yaml` file is as follows:
```yaml
envs:
  MODEL_NAME: moonshotai/Kimi-K2-Instruct

secrets:
  HF_TOKEN: null # Pass with `--secret HF_TOKEN` in CLI

resources:
  image_id: docker:vllm/vllm-openai:v0.10.0
  network_tier: best
  accelerators: H100:8
  cpus: 100+
  memory: 1000+
  ports: 8081

# Uses multi-node for serving on H100s - need at least 16 H100s
num_nodes: 2

setup: |
  pip install blobfile

run: |
  echo "Starting Ray..."
  sudo chmod 777 -R /var/tmp
  HEAD_IP=`echo "$SKYPILOT_NODE_IPS" | head -n1`
  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    ps aux | grep ray | grep 6379 &> /dev/null || ray start --head --disable-usage-stats --port 6379
    sleep 5
  else
    sleep 5
    ps aux | grep ray | grep 6379 &> /dev/null || ray start --address $HEAD_IP:6379 --disable-usage-stats
    # Add sleep to after `ray start` to give ray enough time to daemonize
    sleep 5
  fi

  sleep 10
  echo "Ray cluster started"
  ray status

  echo 'Starting vllm api server...'

  # Set VLLM_HOST_IP to the IP of the current node based on rank
  VLLM_HOST_IP=`echo "$SKYPILOT_NODE_IPS" | sed -n "$((SKYPILOT_NODE_RANK + 1))p"`
  export VLLM_HOST_IP

  # Only head node needs to start the vllm api server
  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    vllm serve $MODEL_NAME \
      --port 8081 \
      --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
      --pipeline-parallel-size $SKYPILOT_NUM_NODES \
      --max-model-len 32768 \
      --trust-remote-code
  else
    sleep infinity
  fi

service:
  replicas: 1
  # An actual request for readiness probe.
  readiness_probe:
    path: /v1/chat/completions
    post_data:
      model: $MODEL_NAME
      messages:
        - role: user
          content: Hello! What is your name?
      max_tokens: 1
```


Due to Kimi-K2's large size (1 trillion total parameters with 32 billion activated parameters), this configuration uses **multi-node serving** with vLLM:

- **Pipeline Parallelism**: The model is distributed across 2 nodes using `pipeline-parallel-size`
- **Tensor Parallelism**: Each node uses 8 H100 GPUs with `tensor-parallel-size`
- **Ray Cluster**: Coordinates the multi-node setup for distributed serving

🎉 **Congratulations!** 🎉 You have now launched the Kimi-K2 LLM on your infra with multi-node serving.

### Chat with Kimi-K2 with OpenAI API

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8081 kimi-k2)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moonshotai/Kimi-K2-Instruct",
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
  }' | jq .
```

To stop the instance:
```console
sky stop kimi-k2
```

To shut down all resources:
```console
sky down kimi-k2
```

## Serving Kimi-K2: scaling up with SkyServe

With no change to the YAML, launch a fully managed service on your infra:
```console
HF_TOKEN=xxx sky serve up kimi-k2.yaml -n kimi-k2 --secret HF_TOKEN
```

Wait until the service is ready:
```console
watch -n10 sky serve status kimi-k2
```

Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint kimi-k2)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moonshotai/Kimi-K2-Instruct",
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
  }' | jq .
```

To shut down all resources:
```console
sky serve down kimi-k2
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).
