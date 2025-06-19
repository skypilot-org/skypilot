# Databricks DBRX: A State-of-the-Art Open LLM

<p align="center">
  <img src="https://www.databricks.com/en-blog-assets/static/2fe1a0af1ee0f6605024a810b604079c/dbrx-blog-header-optimized.png" alt="DBRX Blog Header" height="200">
</p>

[DBRX](https://www.databricks.com/blog/introducing-dbrx-new-state-art-open-llm) is an open, general-purpose LLM created by Databricks. It uses a mixture-of-experts (MoE) architecture with 132B total parameters of which 36B parameters are active on any input.

In this recipe, you will serve `databricks/dbrx-instruct` on your own infra  -- existing Kubernetes cluster or cloud VMs -- with one command.

## Prerequisites

- Go to the [HuggingFace model page](https://huggingface.co/databricks/dbrx-instruct) and request access to the model `databricks/dbrx-instruct`.
- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## SkyPilot YAML

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml
envs:
  MODEL_NAME: databricks/dbrx-instruct
secrets:
  HF_TOKEN: null # Pass with `--secret HF_TOKEN` in CLI

service:
  replicas: 2
  # An actual request for readiness probe.
  readiness_probe:
    path: /v1/chat/completions
    post_data:
      model: $MODEL_NAME
      messages:
        - role: user
          content: Hello! What is your name?
      max_tokens: 1

resources:
  accelerators: {A100-80GB:8, A100-80GB:4, A100:8, A100:16}
  cpus: 32+
  memory: 512+
  use_spot: True
  disk_size: 512  # Ensure model checkpoints (~246GB) can fit.
  disk_tier: best
  ports: 8081  # Expose to internet traffic.

setup: |
  conda activate vllm
  if [ $? -ne 0 ]; then
    conda create -n vllm python=3.10 -y
    conda activate vllm
  fi

  # DBRX merged on master, 3/27/2024
  pip install git+https://github.com/vllm-project/vllm.git@e24336b5a772ab3aa6ad83527b880f9e5050ea2a

  pip install gradio tiktoken==0.6.0 openai

run: |
  conda activate vllm
  echo 'Starting vllm api server...'

  # https://github.com/vllm-project/vllm/issues/3098
  export PATH=$PATH:/sbin

  # NOTE: --gpu-memory-utilization 0.95 needed for 4-GPU nodes.
  python -u -m vllm.entrypoints.openai.api_server \
    --port 8081 \
    --model $MODEL_NAME \
    --trust-remote-code --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
    --gpu-memory-utilization 0.95 \
    2>&1 | tee api_server.log &

  while ! `cat api_server.log | grep -q 'Uvicorn running on'`; do
    echo 'Waiting for vllm api server to start...'
    sleep 5
  done

  echo 'Starting gradio server...'
  git clone https://github.com/vllm-project/vllm.git || true
  python vllm/examples/gradio_openai_chatbot_webserver.py \
    -m $MODEL_NAME \
    --port 8811 \
    --model-url http://localhost:8081/v1
```
</details>

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/tree/master/llm/dbrx/dbrx.yaml).

## Serving DBRX: single instance

Launch a single spot instance to serve DBRX on your infra:
```console
HF_TOKEN=xxx sky launch dbrx.yaml -c dbrx --secret HF_TOKEN
```

<details>
<summary>Example outputs:</summary>

```console
...
I 03-28 08:40:47 optimizer.py:690] == Optimizer ==
I 03-28 08:40:47 optimizer.py:701] Target: minimizing cost
I 03-28 08:40:47 optimizer.py:713] Estimated cost: $2.44 / hour
I 03-28 08:40:47 optimizer.py:713]
I 03-28 08:40:47 optimizer.py:836] Considered resources (1 node):
I 03-28 08:40:47 optimizer.py:906] ----------------------------------------------------------------------------------------------------------------------
I 03-28 08:40:47 optimizer.py:906]  CLOUD        INSTANCE                          vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE      COST ($)   CHOSEN   
I 03-28 08:40:47 optimizer.py:906] ----------------------------------------------------------------------------------------------------------------------
I 03-28 08:40:47 optimizer.py:906]  Azure        Standard_NC96ads_A100_v4[Spot]    96      880       A100-80GB:4    eastus           2.44          ✔      
I 03-28 08:40:47 optimizer.py:906]  AWS          p4d.24xlarge[Spot]                96      1152      A100:8         us-east-2b       4.15                
I 03-28 08:40:47 optimizer.py:906]  Azure        Standard_ND96asr_v4[Spot]         96      900       A100:8         eastus           4.82                
I 03-28 08:40:47 optimizer.py:906]  Azure        Standard_ND96amsr_A100_v4[Spot]   96      1924      A100-80GB:8    southcentralus   5.17                
I 03-28 08:40:47 optimizer.py:906]  GCP          a2-ultragpu-4g[Spot]              48      680       A100-80GB:4    us-east4-c       7.39                
I 03-28 08:40:47 optimizer.py:906]  GCP          a2-highgpu-8g[Spot]               96      680       A100:8         us-central1-a    11.75               
I 03-28 08:40:47 optimizer.py:906]  GCP          a2-ultragpu-8g[Spot]              96      1360      A100-80GB:8    us-east4-c       14.79               
I 03-28 08:40:47 optimizer.py:906]  GCP          a2-megagpu-16g[Spot]              96      1360      A100:16        us-central1-a    22.30               
I 03-28 08:40:47 optimizer.py:906] ----------------------------------------------------------------------------------------------------------------------
...
```

</details>

To run on Kubernetes or use an on-demand instance, pass `--no-use-spot` to the above command.

<details>
<summary>Example outputs with Kubernetes / on-demand instances:</summary>

```console
$ HF_TOKEN=xxx sky launch dbrx.yaml -c dbrx --secret HF_TOKEN --no-use-spot
...
I 03-28 08:47:27 optimizer.py:690] == Optimizer ==
I 03-28 08:47:27 optimizer.py:701] Target: minimizing cost
I 03-28 08:47:27 optimizer.py:713] Estimated cost: $0.0 / hour
I 03-28 08:47:27 optimizer.py:713] 
I 03-28 08:47:27 optimizer.py:836] Considered resources (1 node):
I 03-28 08:47:27 optimizer.py:906] ------------------------------------------------------------------------------------------------------------------
I 03-28 08:47:27 optimizer.py:906]  CLOUD        INSTANCE                    vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE        COST ($)   CHOSEN   
I 03-28 08:47:27 optimizer.py:906] ------------------------------------------------------------------------------------------------------------------
I 03-28 08:47:27 optimizer.py:906]  Kubernetes   32CPU--512GB--8A100         32      512       A100:8         kubernetes         0.00          ✔     
I 03-28 08:47:27 optimizer.py:906]  Azure        Standard_NC96ads_A100_v4    96      880       A100-80GB:4    eastus             14.69               
I 03-28 08:47:27 optimizer.py:906]  Fluidstack   recUYj6oGJCvAvCXC7KQo5Fc7   252     960       A100-80GB:8    generic_1_canada   19.79               
I 03-28 08:47:27 optimizer.py:906]  GCP          a2-ultragpu-4g              48      680       A100-80GB:4    us-central1-a      20.11               
I 03-28 08:47:27 optimizer.py:906]  Paperspace   A100-80Gx8                  96      640       A100-80GB:8    East Coast (NY2)   25.44               
I 03-28 08:47:27 optimizer.py:906]  Azure        Standard_ND96asr_v4         96      900       A100:8         eastus             27.20               
I 03-28 08:47:27 optimizer.py:906]  GCP          a2-highgpu-8g               96      680       A100:8         us-central1-a      29.39               
I 03-28 08:47:27 optimizer.py:906]  Azure        Standard_ND96amsr_A100_v4   96      1924      A100-80GB:8    eastus             32.77               
I 03-28 08:47:27 optimizer.py:906]  AWS          p4d.24xlarge                96      1152      A100:8         us-east-1          32.77               
I 03-28 08:47:27 optimizer.py:906]  GCP          a2-ultragpu-8g              96      1360      A100-80GB:8    us-central1-a      40.22               
I 03-28 08:47:27 optimizer.py:906]  AWS          p4de.24xlarge               96      1152      A100-80GB:8    us-east-1          40.97               
I 03-28 08:47:27 optimizer.py:906]  GCP          a2-megagpu-16g              96      1360      A100:16        us-central1-a      55.74               
I 03-28 08:47:27 optimizer.py:906] ------------------------------------------------------------------------------------------------------------------
...
```

</details>

Wait until the model is ready (this can take 10+ minutes), as indicated by these lines:
```console
...
(task, pid=17433) Waiting for vllm api server to start...
...
(task, pid=17433) INFO:     Started server process [20621]
(task, pid=17433) INFO:     Waiting for application startup.
(task, pid=17433) INFO:     Application startup complete.
(task, pid=17433) INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
...
(task, pid=17433) Running on local URL:  http://127.0.0.1:8811
(task, pid=17433) Running on public URL: https://xxxxxxxxxx.gradio.live
...
(task, pid=17433) INFO 03-28 04:32:50 metrics.py:218] Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 0.0 tokens/s, Running: 0 reqs, Swapped: 0 reqs, Pending: 0 reqs, GPU KV cache usage: 0.0%, CPU KV cache usage: 0.0%
```
🎉 **Congratulations!** 🎉 You have now launched the DBRX Instruct LLM on your infra.

You can play with the model via
- Standard OpenAPI-compatible endpoints (e.g., `/v1/chat/completions`)
- Gradio UI (automatically launched)

To curl `/v1/chat/completions`:
```console
IP=$(sky status --ip dbrx)

curl http://$IP:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "databricks/dbrx-instruct",
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
  }'
```

To use the Gradio UI, open the URL shown in the logs:
```console
(task, pid=17433) Running on public URL: https://xxxxxxxxxx.gradio.live
```

<p align="center">
<img src="https://i.imgur.com/lTfaRpN.gif" alt="Gradio UI serving DBRX" style="height: 600px;">
</p>

To stop the instance:
```console
sky stop dbrx
```

To shut down all resources:
```console
sky down dbrx
```

## Serving DBRX: scaling up with SkyServe

After playing with the model, you can deploy the model with autoscaling and load-balancing using SkyServe.

With no change to the YAML, launch a fully managed service on your infra:
```console
$ HF_TOKEN=xxx sky serve up dbrx.yaml -n dbrx --secret HF_TOKEN
```

Wait until the service is ready:
```console
$ watch -n10 sky serve status dbrx
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME  VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
dbrx  1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                       STATUS  REGION
dbrx          1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
dbrx          2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
$ ENDPOINT=$(sky serve status --endpoint dbrx)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "databricks/dbrx-instruct",
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
  }'
```

To shut down all resources:
```console
$ sky serve down dbrx
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).


