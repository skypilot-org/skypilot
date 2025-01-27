# Run and Serve Janus by DeepSeek with SkyPilot

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

<p align="center">
<img src="https://i.imgur.com/6umSuKw.png" alt="DeepSeek-R1 on SkyPilot" style="width: 70%;">
</p>

On Jan 27, 2025, DeepSeek AI released the [Janus](https://github.com/deepseek-ai/Janus).

DeepSeek-R1 naturally outperforms **state-of-the-art Vision Languag Models** such as LLaVA.

This guide walks through how to run and host DeepSeek-R1 models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public Clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)). 

### Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run DeepSeek-R1 on:

**If your local machine/cluster has GPU**: you can run SkyPilot [directly on existing machines](https://docs.skypilot.co/en/latest/reservations/existing-machines.html) with 

```bash
sky local up
```

**If you want to use Clouds** (15+ clouds are supported):

```bash
sky check 
```
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


### Step 1: Run it with SkyPilot

Now it's time to run deepseek with SkyPilot. The instruction can be dependent on your existing hardware.  

8B: 
```
sky launch janus.yaml \
  -c janus \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN \
  --gpus L4:1
```

### Step 2: Get Results 
Get a single endpoint that load-balances across replicas:

```
ENDPOINT=$(sky status --ip janus)
```

Query the endpoint in a terminal:
7B: 
```
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
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

