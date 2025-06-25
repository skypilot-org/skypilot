# Gemma: Open-source Gemini
![image](https://github.com/skypilot-org/skypilot/assets/6753189/e452c39e-b5ef-4cb2-ab48-053f9e6f67b7)

Google released [Gemma](https://blog.google/technology/developers/gemma-open-models/) and has made a big wave in the AI community.
It opens the opportunity for the open-source community to serve and finetune private Gemini.

## Serve Gemma on any Cloud

Serving Gemma on any cloud is easy with SkyPilot. With [serve.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/gemma/serve.yaml) in this directory, you host the model on any cloud with a single command.

### Prerequisites

1. Apply for access to the Gemma model

Go to the [application page](https://huggingface.co/google/gemma-7b) and click **Acknowledge license** to apply for access to the model weights.


2. Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the Gemma models [here](https://huggingface.co/google/gemma-7b).

3. Install SkyPilot

```bash
pip install "skypilot-nightly[all]"
```
For detailed installation instructions, please refer to the [installation guide](https://docs.skypilot.co/en/latest/getting-started/installation.html).

### Host on a Single Instance

We can host the model with a single instance:
```bash
HF_TOKEN=xxx sky launch -c gemma serve.yaml --secret HF_TOKEN
```

After the cluster is launched, we can access the model with the following command:
```bash
IP=$(sky status --ip gemma)

curl http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

Chat API is also supported:
```bash
IP=$(sky status --ip gemma)

curl http://$IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }'
```

### Scale the Serving with SkyServe


Using the same YAML, we can easily scale the model serving across multiple instances, regions and clouds with SkyServe:
```bash
HF_TOKEN=xxx sky serve up -n gemma serve.yaml --secret HF_TOKEN
```

> Notice the only change is from `sky launch` to `sky serve up`. The same YAML can be used without changes.

After the cluster is launched, we can access the model with the following command:
```bash
ENDPOINT=$(sky serve status --endpoint gemma)

curl http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

Chat API is also supported:
```bash
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }'
```
