# Serve Llama 3.1 on Your Own Infrastructure


<p align="center">
<img src="https://i.imgur.com/kQGzHI6.png" alt="Llama-3.1 on SkyPilot" style="width: 70%;">
</p>

On July 23, 2024, Meta AI released the [Llama 3.1 model family](https://ai.meta.com/blog/meta-llama-3-1/), including a 405B parameter model in both base model and instruction-tuned forms.

Llama 3.1 405B became the most capable open LLM model to date. This is **the first time an open LLM closely rivals state-of-the-art proprietary models** like GPT-4o and Claude 3.5 Sonnet.

This guide walks through how to serve Llama 3.1 models **completely on your infrastructure** (cluster or cloud VPC). Supported infra:

- Local GPU workstation
- Kubernetes cluster
- Cloud accounts ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html))

SkyPilot will be used as the unified framework to launch serving on any (or multiple) infra that you bring.

## Serving Llama 3.1 on your infra

Below is a step-by-step guide to using SkyPilot for testing a new model on a GPU dev node, and then packaging it for one-click deployment across any infrastructure. 

**To skip directly to the packaged deployment YAML for Llama 3.1, see [Step 3: Package and deploy using SkyPilot](#step-3-package-and-deploy-using-skypilot).**

### GPUs required for serving Llama 3.1

Llama 3.1 comes in different sizes, and each size has different GPU requirements. Here is the model-GPU compatibility matrix (applies to both pretrained and instruction tuned models):

| **GPU**         	| **Meta-Llama-3.1-8B**        	| **Meta-Llama-3.1-70B** 	| **Meta-Llama-3.1-405B-FP8**  	|
|-----------------	|------------------------------	|------------------------	|------------------------------	|
| **L4:1**        	| ✅, with `--max-model-len 4096` 	| ❌                      	| ❌                            	|
| **L4:8**        	| ✅                            	| ❌                      	| ❌                            	|
| **A100:8**      	| ✅                            	| ✅                      	| ❌                            	|
| **A100-80GB:8** 	| ✅                            	| ✅                      	| ✅, with `--max-model-len 4096` 	|


### Step 0: Bring your infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run Llama 3.1 on:

**If your local machine is a GPU node**: use this command to up a lightweight kubernetes cluster:

```bash
sky local up
```

**If you have a Kubernetes GPU cluster** (e.g., on-prem, EKS / GKE / AKS / ...):

```bash
# Should show Enabled if you have ~/.kube/config set up.
sky check kubernetes
```

**If you want to use clouds** (e.g., reserved instances): 12+ clouds are supported:

```bash
sky check
```

See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.

### Step 1: Get a GPU dev node (pod or VM)

> **Tip:** If you simply want the final deployment YAML, skip directly to [Step 3](#step-3-package-and-deploy-using-skypilot).

One command to get a GPU dev pod/VM:
```bash
sky launch -c llama --gpus A100-80GB:8
```
If you are using local machine or Kubernetes, the above will create a pod. If you are using clouds, the above will create a VM.

You can add a `-r / --retry-until-up` flag to have SkyPilot auto-retry to guard against out-of-capacity errors.


> **Tip:** Vary the `--gpus` flag to get different GPU types and counts. For example, `--gpus H100:8` gets you a pod with 8x H100 GPUs.
>
> You can run `sky show-gpus` to see all available GPU types on your infra.


Once provisioned, you can easily connect to it to start dev work. Two recommended methods:
- Open up VSCode, click bottom left, `Connect to Host`, type `llama`
- Or, SSH into it with `ssh llama`

### Step 2: Inside the dev node, test serving

Once logged in, run the following to install vLLM and run it (which automatically pulls the model weights from HuggingFace):
```bash
pip install vllm==0.5.3.post1 huggingface

# Paste your HuggingFace token to get access to Meta Llama repos:
# https://huggingface.co/collections/meta-llama/llama-31-669fc079a0c406a149a5738f
huggingface-cli login
```

We are now ready to start serving.  If you have N=8 GPUs
```bash
vllm serve meta-llama/Meta-Llama-3.1-8B-Instruct --tensor-parallel-size 8
```
Change the `--tensor-parallel-size` to the number of GPUs you have.

Tip: available model names can be found [here](https://huggingface.co/collections/meta-llama/llama-31-669fc079a0c406a149a5738f) and below.
- Pretrained:
  - Meta-Llama-3.1-8B
  - Meta-Llama-3.1-70B
  - Meta-Llama-3.1-405B-FP8
- Instruction tuned:
  - Meta-Llama-3.1-8B-Instruct
  - Meta-Llama-3.1-70B-Instruct
  - Meta-Llama-3.1-405B-Instruct-FP8


The full precision 405B model Meta-Llama-3.1-405B requires multi-node inference and is work in progress - join the [SkyPilot community Slack](https://slack.skypilot.co/) for discussions.

Test that `curl` works from within the node:
```bash
ENDPOINT=127.0.0.1:8000
curl http://$ENDPOINT/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
    "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
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
```

🎉 Voila! You should be getting results like this:

<p align="center">
<img src="https://i.imgur.com/yY0h3lZ.png" alt="Llama-3.1 on SkyPilot" style="width: 50%;">
</p>

When you are done, terminate your cluster with:
```
sky down llama
```

### Step 3: Package and deploy using SkyPilot

Now that we verified the model is working, let's package it for hands-free deployment.

Whichever infra you use for GPUs, SkyPilot abstracts away the mundane infra tasks (e.g., setting up services on K8s, opening up ports for cloud VMs), making AI models super easy to deploy via one command.

[Deploying via SkyPilot](https://docs.skypilot.co/en/latest/serving/sky-serve.html) has several key benefits:
- Control node & replicas completely stay in your infra
- Automatic load-balancing across multiple replicas
- Automatic recovery of replicas
- Replicas can use different infras to save significant costs
  - e.g., a mix of clouds, or a mix of reserved & spot GPUs 

<details>
<summary>Click to see the YAML: <code>serve.yaml</code>.</summary>

```yaml

envs:
  MODEL_NAME: meta-llama/Meta-Llama-3.1-8B-Instruct
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
  disk_size: 1000  # Ensure model checkpoints can fit.
  disk_tier: best
  ports: 8081  # Expose to internet traffic.

setup: |
  pip install vllm==0.5.3post1
  pip install vllm-flash-attn==2.5.9.post1
  # Install Gradio for web UI.
  pip install gradio openai

run: |
  echo 'Starting vllm api server...'
  
  vllm serve $MODEL_NAME \
    --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
    --max-model-len 4096 \
    --port 8081 \
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

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-3_1/llama-3_1.yaml).

Launch a fully managed service with load-balancing and auto-recovery:

```
HF_TOKEN=xxx sky serve up llama-3_1.yaml -n llama31 --secret HF_TOKEN --gpus L4:1 --env MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct
```

Wait until the service is ready:

```
watch -n10 sky serve status llama31
```

Get a single endpoint that load-balances across replicas:

```
ENDPOINT=$(sky serve status --endpoint llama31)
```

Query the endpoint in a terminal:
```
curl -L http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
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


<details>
<summary>Click to see the output</summary>

```console
{
  "id": "chat-5cdbc2091c934e619e56efd0ed85e28f",
  "object": "chat.completion",
  "created": 1721784853,
  "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "I am a helpful assistant, here to provide information and assist with tasks to the best of my abilities. I'm a computer program designed to simulate conversation and answer questions on a wide range of topics. I can help with things like:\n\n* Providing definitions and explanations\n* Answering questions on history, science, and technology\n* Generating text and ideas\n* Translating languages\n* Offering suggestions and recommendations\n* And more!\n\nI'm constantly learning and improving, so feel free to ask me anything. What can I help you with today?",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "total_tokens": 136,
    "completion_tokens": 111
  }
}
```

</details>

🎉 **Congratulations!** You are now serving a Llama 3.1 8B model across two replicas. To recap, all model replicas **stay in your own private infrastructure** and SkyPilot ensures they are **healthy and available**.


Details on autoscaling, rolling updates, and more in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).

When you are done, shut down all resources:

```
sky serve down llama31
```

## Bonus: Finetuning Llama 3.1
You can also finetune Llama 3.1 on your infra with SkyPilot. Check out our [blog](https://blog.skypilot.co/finetune-llama-3_1-on-your-infra/) for more details.
