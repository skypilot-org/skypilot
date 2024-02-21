# Serve Your Private Gemma

Google released [Gemma](https://blog.google/technology/developers/gemma-open-models/) and has made a big wave in the AI community.
It opens the opportunity for the open-source community to serve and finetune private Gemini.

## Serve Gemma on any Cloud

### Prerequsite

1. Apply for the access to the Gemma model

Go to the [application page](https://huggingface.co/google/gemma-7b) and apply for the access to the model weights.


2. Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the Gemma models [here](https://huggingface.co/google/gemma-7b).


### Host on a Single Instance

We can host the model with a single instance:
```bash
sky launch -c gemma serve.yaml --env HF_TOKEN="xxx"
```

After the cluster is launched, we can access the model with the following command:
```bash
IP=$(sky status --ip gemma)

curl -L http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

We can also access the model with chat completion API:

```bash
IP=$(sky status --ip gemma)

curl -L http://$IP:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }' | jq .
```

### Scale the Serving with SkyServe


We can scale the model serving across multiple instances, regions and clouds with SkyServe:
```bash
sky serve up -n gemma serve.yaml --env HF_TOKEN="xxx"
```


After the cluster is launched, we can access the model with the following command:
```bash
ENDPOINT=$(sky serve status --endpoint gemma)

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

We can also access the model with chat completion API:

```bash
ENDPOINT=$(sky serve status --endpoint gemma)

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b",
      "messages": [
        {
          "role": "user",
          "content": "Hello! What is your name?"
        }
      ],
      "max_tokens": 25
  }' | jq .
```
