
<!-- $REMOVE -->
# Point, Launch, and Serve Vision Llama 3.2 on Kubernetes or Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Vision Llama 3.2 -->


[Llama 3.2](https://ai.meta.com/blog/llama-3-2-connect-2024-vision-edge-mobile-devices/) family was released by Meta on Sep 25, 2024. It not only includes the latest improved (and smaller) LLM models for chat, but also includes multimodal vision-language models. Let's _point and launch_ it with SkyPilot.

* [Llama 3.2 release](https://ai.meta.com/blog/llama-3-2-connect-2024-vision-edge-mobile-devices/)



## Why use SkyPilot?

* **Point, launch, and serve**: simply point to the cloud/Kubernetes cluster you have access to, and launch the model there with a single command.
* No lock-in: run on any supported cloud — AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI
* Everything stays in your cloud account (your VMs & buckets)
* No one else sees your chat history
* Pay absolute minimum — no managed solution markups
* Freely choose your own model size, GPU type, number of GPUs, etc, based on scale and budget.

…and you get all of this with 1 click — let SkyPilot automate the infra.


## Prerequisites

- Go to the [HuggingFace model page](https://huggingface.co/meta-llama/) and request access to the model [meta-llama/Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) and [meta-llama/Llama-3.2-11B-Vision](https://huggingface.co/meta-llama/Llama-3.2-11B-Vision).
- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## SkyPilot YAML

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml
envs:
  MODEL_NAME: meta-llama/Llama-3.2-3B-Instruct
  # MODEL_NAME: meta-llama/Llama-3.2-3B-Vision
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
  accelerators: {L4:1, L40S:1, L40:1, A10g:1, A10:1, A100:1, H100:1}
  # accelerators: {L4, A10g, A10, L40, A40, A100, A100-80GB} # We can use cheaper accelerators for 8B model.
  cpus: 8+
  disk_size: 512  # Ensure model checkpoints can fit.
  disk_tier: best
  ports: 8081  # Expose to internet traffic.

setup: |
  # Install huggingface transformers for the support of Llama 3.2
  pip install git+https://github.com/huggingface/transformers.git@f0eabf6c7da2afbe8425546c092fa3722f9f219e
  pip install vllm==0.6.2

run: |
  echo 'Starting vllm api server...'

  vllm serve $MODEL_NAME \
    --port 8081 \
    --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
    --max-model-len 4096 \
    2>&1

```

</details>

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-3_2/llama3_2.yaml).

## Point and Launch Llama 3.2

Launch a single spot instance to serve Llama 3.2 on your infra:
```console
$ HF_TOKEN=xxx sky launch llama3_2.yaml -c llama3_2 --secret HF_TOKEN
```

```console
...
------------------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE                       vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN
------------------------------------------------------------------------------------------------------------------
 Kubernetes   4CPU--16GB--1L4                4       16        L4:1           kubernetes      0.00          ✔
 RunPod       1x_L4_SECURE                   4       24        L4:1           CA              0.44
 GCP          g2-standard-4                  4       16        L4:1           us-east4-a      0.70
 AWS          g6.xlarge                      4       16        L4:1           us-east-1       0.80
 AWS          g5.xlarge                      4       16        A10G:1         us-east-1       1.01
 RunPod       1x_L40_SECURE                  16      48        L40:1          CA              1.14
 Fluidstack   L40_48GB::1                    32      60        L40:1          CANADA          1.15
 AWS          g6e.xlarge                     4       32        L40S:1         us-east-1       1.86
 Cudo         sapphire-rapids-h100_1x4v8gb   4       8         H100:1         ca-montreal-3   2.86
 Fluidstack   H100_PCIE_80GB::1              28      180       H100:1         CANADA          2.89
 Azure        Standard_NV36ads_A10_v5        36      440       A10:1          eastus          3.20
 GCP          a2-highgpu-1g                  12      85        A100:1         us-central1-a   3.67
 RunPod       1x_H100_SECURE                 16      80        H100:1         CA              4.49
 Azure        Standard_NC40ads_H100_v5       40      320       H100:1         eastus          6.98
------------------------------------------------------------------------------------------------------------------
```


Wait until the model is ready (this can take 10+ minutes).

🎉 **Congratulations!** 🎉 You have now launched the Llama 3.2 Instruct LLM on your infra.

### Chat with Llama 3.2 with OpenAI API

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8081 llama3_2)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.2-3B-Instruct",
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
Example outputs:
```console
{
  "id": "chat-e7b6d2a2d2934bcab169f82812601baf",
  "object": "chat.completion",
  "created": 1727291780,
  "model": "meta-llama/Llama-3.2-3B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "I'm an artificial intelligence model known as Llama. Llama stands for \"Large Language Model Meta AI.\"",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "total_tokens": 68,
    "completion_tokens": 23
  },
  "prompt_logprobs": null
}
```

To stop the instance:
```console
sky stop llama3_2
```

To shut down all resources:
```console
sky down llama3_2
```

## Point and Launch Vision Llama 3.2

Let's launch a vision llama now! The multimodal capacity of Llama-3.2 could open up a lot of new use cases. We will go with the largest 11B model here.

```console
$ HF_TOKEN=xxx sky launch llama3_2-vision-11b.yaml -c llama3_2-vision --secret HF_TOKEN
```

```console
------------------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE                       vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN
------------------------------------------------------------------------------------------------------------------
 Kubernetes   2CPU--8GB--1H100               2       8         H100:1         kubernetes      0.00          ✔
 RunPod       1x_L40_SECURE                  16      48        L40:1          CA              1.14
 Fluidstack   L40_48GB::1                    32      60        L40:1          CANADA          1.15
 AWS          g6e.xlarge                     4       32        L40S:1         us-east-1       1.86
 RunPod       1x_A100-80GB_SECURE            8       80        A100-80GB:1    CA              1.99
 Cudo         sapphire-rapids-h100_1x2v4gb   2       4         H100:1         ca-montreal-3   2.83
 Fluidstack   H100_PCIE_80GB::1              28      180       H100:1         CANADA          2.89
 GCP          a2-highgpu-1g                  12      85        A100:1         us-central1-a   3.67
 Azure        Standard_NC24ads_A100_v4       24      220       A100-80GB:1    eastus          3.67
 RunPod       1x_H100_SECURE                 16      80        H100:1         CA              4.49
 GCP          a2-ultragpu-1g                 12      170       A100-80GB:1    us-central1-a   5.03
 Azure        Standard_NC40ads_H100_v5       40      320       H100:1         eastus          6.98
------------------------------------------------------------------------------------------------------------------
```


### Chat with Vision Llama 3.2

```console
ENDPOINT=$(sky status --endpoint 8081 llama3_2-vision)

curl http://$ENDPOINT/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer token' \
    --data '{
        "model": "meta-llama/Llama-3.2-11B-Vision-Instruct",
        "messages": [
        {
            "role": "user",
            "content": [
                {"type" : "text", "text": "Turn this logo into ASCII art."},
                {"type": "image_url", "image_url": {"url": "https://pbs.twimg.com/profile_images/1584596138635632640/HWexMoH5_400x400.jpg"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```

Example output (parsed):

1. Output 1
```console
-------------
-        -
-   -   -
-   -   -
-        -
-------------
```

2. Output 2
```
        ^_________
       /          \\
      /            \\
     /______________\\
     |               |
     |               |
     |_______________|
       \\            /
        \\          /
         \\________/
```

<details>
<summary>Raw output</summary>

```console
{
  "id": "chat-c341b8a0b40543918f3bb2fef68b0952",
  "object": "chat.completion",
  "created": 1727295337,
  "model": "meta-llama/Llama-3.2-11B-Vision-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Sure, here is the logo in ASCII art:\n\n------------- \n-        - \n-   -   - \n-   -   - \n-        - \n------------- \n\nNote that this is a very simple representation and does not capture all the details of the original logo.",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 18,
    "total_tokens": 73,
    "completion_tokens": 55
  },
  "prompt_logprobs": null
}
```

</details>


## Serving Llama-3: scaling up with SkyServe

After playing with the model, you can deploy the model with autoscaling and load-balancing using SkyServe.

With no change to the YAML, launch a fully managed service on your infra:
```console
HF_TOKEN=xxx sky serve up llama3_2-vision-11b.yaml -n llama3_2 --secret HF_TOKEN
```

Wait until the service is ready:
```console
watch -n10 sky serve status llama3_2
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME  VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
llama3_2  1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                       STATUS  REGION
llama3_2          1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'A100-80GB': 8})  READY   us-east4
llama3_2          2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'A100-80GB': 8})  READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint llama3_2)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl http://$ENDPOINT/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer token' \
    --data '{
        "model": "meta-llama/Llama-3.2-11B-Vision-Instruct",
        "messages": [
        {
            "role": "user",
            "content": [
                {"type" : "text", "text": "Covert this logo to ASCII art"},
                {"type": "image_url", "image_url": {"url": "https://pbs.twimg.com/profile_images/1584596138635632640/HWexMoH5_400x400.jpg"}}
            ]
        }],
        "max_tokens": 2048
    }' | jq .
```

To shut down all resources:
```console
sky serve down llama3
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).


## Developing and Finetuning Llama 3 series

SkyPilot also simplifies the development and finetuning of Llama 3 series. Check out the development and finetuning guides: [Develop](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-3_1/README.md) and [Finetune](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-3_1-finetuning/readme.md).
