<!-- $REMOVE -->
# Scale Serving Llama 3 on Any Cloud or Kubernetes with SkyPilot
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Llama 3: Open LLM from Meta -->


<p align="center">
<img src="https://i.imgur.com/1NEZs9f.png" alt="Llama-3 x SkyPilot" style="width: 50%;">
</p>

[Llama-3](https://github.com/meta-llama/llama3) is the latest top open-source LLM from Meta. It has been released with a license that authorizes commercial use. You can deploy a private Llama-3 chatbot with SkyPilot in your own cloud with just one simple command.

* [Llama-3 release](https://github.com/meta-llama/llama3)
* [Llama-3 blog](https://ai.meta.com/blog/meta-llama-3/)



## Why use SkyPilot vs. commercial hosted solutions?

* No lock-in: run on any supported cloud - AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI
* Everything stays in your cloud account (your VMs & buckets)
* No one else sees your chat history
* Pay absolute minimum — no managed solution markups
* Freely choose your own model size, GPU type, number of GPUs, etc, based on scale and budget.

…and you get all of this with 1 click — let SkyPilot automate the infra.


## Prerequisites

- Go to the [HuggingFace model page](https://huggingface.co/meta-llama/Meta-Llama-3-70B-Instruct) and request access to the model `meta-llama/Meta-Llama-3-70B-Instruct`.
- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## SkyPilot YAML

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml

envs:
  MODEL_NAME: meta-llama/Meta-Llama-3-70B-Instruct
  # MODEL_NAME: meta-llama/Meta-Llama-3-8B-Instruct
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
  accelerators: {L4:8, A10g:8, A10:8, A100:4, A100:8, A100-80GB:2, A100-80GB:4, A100-80GB:8}
  # accelerators: {L4, A10g, A10, L40, A40, A100, A100-80GB} # We can use cheaper accelerators for 8B model.
  cpus: 32+
  use_spot: True
  disk_size: 512  # Ensure model checkpoints can fit.
  disk_tier: best
  ports: 8081  # Expose to internet traffic.

setup: |
  conda activate vllm
  if [ $? -ne 0 ]; then
    conda create -n vllm python=3.10 -y
    conda activate vllm
  fi

  pip install vllm==0.4.2
  # Install Gradio for web UI.
  pip install gradio openai
  pip install flash-attn==2.5.9.post1


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
    --max-num-seqs 64 \
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
    --model-url http://localhost:8081/v1 \
    --stop-token-ids 128009,128001

```

</details>

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-3/llama3.yaml).

## Serving Llama-3: single instance

Launch a single spot instance to serve Llama-3 on your infra:
```console
HF_TOKEN=xxx sky launch llama3.yaml -c llama3 --secret HF_TOKEN
```

<details>
<summary>Example outputs:</summary>

```console
...
I 04-18 16:31:30 optimizer.py:693] == Optimizer ==
I 04-18 16:31:30 optimizer.py:704] Target: minimizing cost
I 04-18 16:31:30 optimizer.py:716] Estimated cost: $1.2 / hour
I 04-18 16:31:30 optimizer.py:716]
I 04-18 16:31:30 optimizer.py:839] Considered resources (1 node):
I 04-18 16:31:30 optimizer.py:909] -----------------------------------------------------------------------------------------------------------------
I 04-18 16:31:30 optimizer.py:909]  CLOUD   INSTANCE                          vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE      COST ($)   CHOSEN
I 04-18 16:31:30 optimizer.py:909] -----------------------------------------------------------------------------------------------------------------
I 04-18 16:31:30 optimizer.py:909]  Azure   Standard_NC48ads_A100_v4[Spot]    48      440       A100-80GB:2    eastus           1.22          ✔
I 04-18 16:31:30 optimizer.py:909]  AWS     g6.48xlarge[Spot]                 192     768       L4:8           us-east-1b       1.43
I 04-18 16:31:30 optimizer.py:909]  Azure   Standard_NC96ads_A100_v4[Spot]    96      880       A100-80GB:4    eastus           2.44
I 04-18 16:31:30 optimizer.py:909]  AWS     g5.48xlarge[Spot]                 192     768       A10G:8         us-east-2b       2.45
I 04-18 16:31:30 optimizer.py:909]  GCP     g2-standard-96[Spot]              96      384       L4:8           asia-east1-a     2.49
I 04-18 16:31:30 optimizer.py:909]  Azure   Standard_ND96asr_v4[Spot]         96      900       A100:8         eastus           4.82
I 04-18 16:31:30 optimizer.py:909]  GCP     a2-highgpu-4g[Spot]               48      340       A100:4         europe-west4-a   4.82
I 04-18 16:31:30 optimizer.py:909]  AWS     p4d.24xlarge[Spot]                96      1152      A100:8         us-east-2b       4.90
I 04-18 16:31:30 optimizer.py:909]  Azure   Standard_ND96amsr_A100_v4[Spot]   96      1924      A100-80GB:8    southcentralus   5.17
I 04-18 16:31:30 optimizer.py:909]  GCP     a2-ultragpu-4g[Spot]              48      680       A100-80GB:4    us-east4-c       7.39
I 04-18 16:31:30 optimizer.py:909]  GCP     a2-highgpu-8g[Spot]               96      680       A100:8         europe-west4-a   9.65
I 04-18 16:31:30 optimizer.py:909]  GCP     a2-ultragpu-8g[Spot]              96      1360      A100-80GB:8    us-east4-c       14.79
I 04-18 16:31:30 optimizer.py:909] -----------------------------------------------------------------------------------------------------------------
I 04-18 16:31:30 optimizer.py:909]
...
```

</details>

To run on Kubernetes or use an on-demand instance, pass `--no-use-spot` to the above command.

<details>
<summary>Example outputs with Kubernetes / on-demand instances:</summary>

```console
$ HF_TOKEN=xxx sky launch llama3.yaml -c llama3 --secret HF_TOKEN --no-use-spot
...
I 04-18 16:34:13 optimizer.py:693] == Optimizer ==
I 04-18 16:34:13 optimizer.py:704] Target: minimizing cost
I 04-18 16:34:13 optimizer.py:716] Estimated cost: $5.0 / hour
I 04-18 16:34:13 optimizer.py:716]
I 04-18 16:34:13 optimizer.py:839] Considered resources (1 node):
I 04-18 16:34:13 optimizer.py:909] ------------------------------------------------------------------------------------------------------------------
I 04-18 16:34:13 optimizer.py:909]  CLOUD        INSTANCE                    vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE        COST ($)   CHOSEN
I 04-18 16:34:13 optimizer.py:909] ------------------------------------------------------------------------------------------------------------------
I 04-18 16:34:13 optimizer.py:909]  Kubernetes   32CPU--512GB--8A100         32      512       A100:8         kubernetes         0.00          ✔
I 04-18 16:34:13 optimizer.py:909]  Fluidstack   recE2ZDQmqR9HBKYs5xSnjtPw   64      240       A100-80GB:2    generic_1_canada   4.96
I 04-18 16:34:13 optimizer.py:909]  Fluidstack   recUiB2e6s3XDxwE9           60      440       A100:4         calgary_1_canada   5.88
I 04-18 16:34:13 optimizer.py:909]  Azure        Standard_NC48ads_A100_v4    48      440       A100-80GB:2    eastus             7.35
I 04-18 16:34:13 optimizer.py:909]  GCP          g2-standard-96              96      384       L4:8           us-east4-a         7.98
I 04-18 16:34:13 optimizer.py:909]  Fluidstack   recWGm4oJ9AB3XVPxzRaujgbx   126     480       A100-80GB:4    generic_1_canada   9.89
I 04-18 16:34:13 optimizer.py:909]  Paperspace   A100-80Gx4                  46      320       A100-80GB:4    East Coast (NY2)   12.72
I 04-18 16:34:13 optimizer.py:909]  AWS          g6.48xlarge                 192     768       L4:8           us-east-1          13.35
I 04-18 16:34:13 optimizer.py:909]  GCP          a2-highgpu-4g               48      340       A100:4         us-central1-a      14.69
I 04-18 16:34:13 optimizer.py:909]  Azure        Standard_NC96ads_A100_v4    96      880       A100-80GB:4    eastus             14.69
I 04-18 16:34:13 optimizer.py:909]  AWS          g5.48xlarge                 192     768       A10G:8         us-east-1          16.29
I 04-18 16:34:13 optimizer.py:909]  Fluidstack   recUYj6oGJCvAvCXC7KQo5Fc7   252     960       A100-80GB:8    generic_1_canada   19.79
I 04-18 16:34:13 optimizer.py:909]  GCP          a2-ultragpu-4g              48      680       A100-80GB:4    us-central1-a      20.11
I 04-18 16:34:13 optimizer.py:909]  Paperspace   A100-80Gx8                  96      640       A100-80GB:8    East Coast (NY2)   25.44
I 04-18 16:34:13 optimizer.py:909]  Azure        Standard_ND96asr_v4         96      900       A100:8         eastus             27.20
I 04-18 16:34:13 optimizer.py:909]  GCP          a2-highgpu-8g               96      680       A100:8         us-central1-a      29.39
I 04-18 16:34:13 optimizer.py:909]  Azure        Standard_ND96amsr_A100_v4   96      1924      A100-80GB:8    eastus             32.77
I 04-18 16:34:13 optimizer.py:909]  AWS          p4d.24xlarge                96      1152      A100:8         us-east-1          32.77
I 04-18 16:34:13 optimizer.py:909]  GCP          a2-ultragpu-8g              96      1360      A100-80GB:8    us-central1-a      40.22
I 04-18 16:34:13 optimizer.py:909]  AWS          p4de.24xlarge               96      1152      A100-80GB:8    us-east-1          40.97
I 04-18 16:34:13 optimizer.py:909] ------------------------------------------------------------------------------------------------------------------
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
🎉 **Congratulations!** 🎉 You have now launched the Llama-3 Instruct LLM on your infra.

You can play with the model via
- Standard OpenAPI-compatible endpoints (e.g., `/v1/chat/completions`)
- Gradio UI (automatically launched)

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8081 llama3)

# We need to manually specify the stop_token_ids to make sure the model finish
# on <|eot_id|>.
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-70B-Instruct",
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
    "stop_token_ids": [128009,  128001]
  }'
```

To use the Gradio UI, open the URL shown in the logs:
```console
(task, pid=17433) Running on public URL: https://xxxxxxxxxx.gradio.live
```


<p align="center">
<img src="https://i.imgur.com/zPpY2Bg.gif" alt="Gradio UI serving Llama-3" style="width: 80%;">
</p>

To stop the instance:
```console
sky stop llama3
```

To shut down all resources:
```console
sky down llama3
```

**Note**: If you would like to try the 8B model, you can use the following accelerators:
```yaml
resources:
  accelerators: {L4, A10g, A10, L40, A40, A100, A100-80GB}
```

## Serving Llama-3: scaling up with SkyServe

After playing with the model, you can deploy the model with autoscaling and load-balancing using SkyServe.

With no change to the YAML, launch a fully managed service on your infra:
```console
HF_TOKEN=xxx sky serve up llama3.yaml -n llama3 --secret HF_TOKEN
```

Wait until the service is ready:
```console
watch -n10 sky serve status llama3
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME  VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
llama3  1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                       STATUS  REGION
llama3          1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
llama3          2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint llama3)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl -L $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-70B-Instruct",
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
sky serve down llama3
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).


### **Optional**: Connect a GUI to your Llama-3 endpoint



It is also possible to access the Code Llama service with a separate GUI frontend, so the user requests send to the GUI will be load-balanced across replicas.

1. Start the chat web UI:
```bash
sky launch -c llama3-gui ./gui.yaml --env ENDPOINT=$(sky serve status --endpoint llama3)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```



## Finetuning Llama-3

You can finetune Llama-3 on your own data. We have an tutorial for finetunning Llama-2 for Vicuna on SkyPilot, which can be adapted for Llama-3. You can find the tutorial [here](https://docs.skypilot.co/en/latest/gallery/tutorials/finetuning.html) and a detailed blog post [here](https://blog.skypilot.co/finetuning-llama2-operational-guide/).
