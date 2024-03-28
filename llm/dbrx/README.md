# Databricks DBRX: A State-of-the-Art Open LLM

<p align="center">
  <img src="https://www.databricks.com/en-blog-assets/static/2fe1a0af1ee0f6605024a810b604079c/dbrx-blog-header-optimized.png" alt="DBRX Blog Header" height="200">
</p>

[DBRX](https://www.databricks.com/blog/introducing-dbrx-new-state-art-open-llm) is an open, general-purpose LLM created by Databricks. It uses a mixture-of-experts (MoE) architecture with 132B total parameters of which 36B parameters are active on any input.

In this recipe, you will serve `databricks/dbrx-instruct` on your own infra  -- existing Kubernetes cluster or cloud VMs -- with one command.

## Prerequisites

- Go to the [HuggingFace model page](https://huggingface.co/databricks/dbrx-instruct) and request access to the model `databricks/dbrx-instruct`.
- Check that you have installed SkyPilot ([docs](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## Serving DBRX: single instance

Launch a single spot instance of DBRX on your infra:
```console
HF_TOKEN=xxx sky launch dbrx.yaml -c dbrx --env HF_TOKEN
```
> To use an on-demand instance instead, pass `--use-spot False` to the above command.

<details>
<summary>Example outputs:</summary>

```console
...
I 03-27 21:08:53 optimizer.py:690] == Optimizer ==
I 03-27 21:08:53 optimizer.py:701] Target: minimizing cost
I 03-27 21:08:53 optimizer.py:713] Estimated cost: $4.1 / hour
I 03-27 21:08:53 optimizer.py:713]
I 03-27 21:08:53 optimizer.py:836] Considered resources (1 node):
I 03-27 21:08:53 optimizer.py:906] -----------------------------------------------------------------------------------------------------
I 03-27 21:08:53 optimizer.py:906]  CLOUD   INSTANCE               vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN
I 03-27 21:08:53 optimizer.py:906] -----------------------------------------------------------------------------------------------------
I 03-27 21:08:53 optimizer.py:906]  AWS     p4d.24xlarge[Spot]     96      1152      A100:8         us-east-2b      4.13          ✔
I 03-27 21:08:53 optimizer.py:906]  GCP     a2-ultragpu-4g[Spot]   48      680       A100-80GB:4    us-east4-c      7.39
I 03-27 21:08:53 optimizer.py:906]  GCP     a2-highgpu-8g[Spot]    96      680       A100:8         us-central1-a   11.75
I 03-27 21:08:53 optimizer.py:906]  GCP     a2-ultragpu-8g[Spot]   96      1360      A100-80GB:8    us-east4-c      14.79
I 03-27 21:08:53 optimizer.py:906]  GCP     a2-megagpu-16g[Spot]   96      1360      A100:16        us-central1-a   22.30
I 03-27 21:08:53 optimizer.py:906] -----------------------------------------------------------------------------------------------------
...
```

</details>

You can play with the model via
- Standard OpenAPI-compatible endpoints (e.g., `/v1/chat/completions`)
- Gradio UI (automatically launched)

To curl `/v1/chat/completions`:
```console
IP=$(sky status --ip dbrx)
curl $IP:8081/v1/models
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
(task, pid=17415) Running on local URL:  http://127.0.0.1:8811
...
(task, pid=17415) Running on public URL: https://0000xxxxxxxx.gradio.live
...
(task, pid=17415) INFO 03-28 03:56:36 metrics.py:218] Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 0.0 tokens/s, Running: 0 reqs, Swapped: 0 reqs, Pending: 0 reqs, GPU KV cache usage: 0.0%, CPU KV cache usage: 0.0%
(task, pid=17415) INFO 03-28 03:56:46 metrics.py:218] Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 0.0 tokens/s, Running: 0 reqs, Swapped: 0 reqs, Pending: 0 reqs, GPU KV cache usage: 0.0%, CPU KV cache usage: 0.0%
...
```

<img src="https://i.imgur.com/BZszerX.png" alt="Gradio UI serving DBRX" style="height: 600px;">

To shut down all resources:
```console
sky down dbrx
```

## Serving DBRX: scaling up with SkyServe

After playing with the model, you can deploy the model with autoscaling and load-balancing using SkyServe.

With no change to the YAML, launch a service on your infra:
```console
HF_TOKEN=xxx sky serve up dbrx.yaml -n dbrx --env HF_TOKEN
```

Get a single endpoint that load-balances across replicas:
```console
sky serve status --endpoint dbrx
```

To shut down all resources:
```console
sky serve down dbrx
```

See more details in [SkyServe docs](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html).


