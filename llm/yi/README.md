# Serving Yi on Your Own Kubernetes or Cloud

🤖 The Yi series models are the next generation of open-source large language models trained from scratch by [01.AI](https://www.lingyiwanwu.com/en).

**Update (Sep 19, 2024) -** SkyPilot now supports the [**Yi**](https://01-ai.github.io/) model(Yi-Coder Yi-1.5)! 

<p align="center">
    <img src="https://raw.githubusercontent.com/01-ai/Yi/main/assets/img/coder/bench1.webp" alt="yi" width="600"/>
</p>

## Why use SkyPilot to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across Kubernetes clusters and multiple regions/clouds.
* Pay absolute minimum — SkyPilot picks the cheapest resources across Kubernetes clusters and regions/clouds. No managed solution markups.
* Scale up to multiple replicas across different locations and accelerators, all served with a single endpoint 
* Everything stays in your Kubernetes or cloud account (your VMs & buckets)
* Completely private - no one else sees your chat history


## Running Yi model with SkyPilot

After [installing SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html), run your own Yi model on vLLM with SkyPilot in 1-click:

1. Start serving Yi-1.5 34B on a single instance with any available GPU in the list specified in [yi15-34b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/yi/yi15-34b.yaml) with a vLLM powered OpenAI-compatible endpoint (You can also switch to [yicoder-9b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/yi/yicoder-9b.yaml) or [other model](https://github.com/skypilot-org/skypilot/tree/master/llm/yi) for a smaller model):

```console
sky launch -c yi yi15-34b.yaml
```
2. Send a request to the endpoint for completion:
```bash
ENDPOINT=$(sky status --endpoint 8000 yi)

curl http://$ENDPOINT/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "01-ai/Yi-1.5-34B-Chat",
      "prompt": "Who are you?",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

3. Send a request for chat completion:
```bash
curl http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "01-ai/Yi-1.5-34B-Chat",
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
      "max_tokens": 512
  }' | jq -r '.choices[0].message.content'
```
