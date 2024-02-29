# Serve and Finetune Your Gemma on Any Cloud
![image](https://github.com/skypilot-org/skypilot/assets/6753189/e452c39e-b5ef-4cb2-ab48-053f9e6f67b7)

Google released [Gemma](https://blog.google/technology/developers/gemma-open-models/) and has made a big wave in the AI community.
It opens the opportunity for the open-source community to serve and finetune private Gemini.

## Serve Gemma on any Cloud

Serving Gemma on any cloud is easy with SkyPilot. With [serve.yaml](serve.yaml) in this directory, you host the model on any cloud with a single command.

### Prerequsites

1. Apply for access to the Gemma model

Go to the [application page](https://huggingface.co/google/gemma-7b) and click **Acknowledge license** to apply for access to the model weights.


2. Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the Gemma models [here](https://huggingface.co/google/gemma-7b).

3. Install SkyPilot

```bash
pip install "skypilot-nightly[all]"
```
For detailed installation instructions, please refer to the [installation guide](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

### Host on a Single Instance

We can host the model with a single instance:
```bash
HF_TOKEN="xxx" sky launch -c gemma serve.yaml --env HF_TOKEN
```

After the cluster is launched, we can access the model with the following command:
```bash
ENDPOINT=$(sky status --endpoint 8000 gemma)

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

Chat API is also supported:
```bash
ENDPOINT=$(sky status --endpoint 8000 gemma)

curl -L http://$ENDPOINT/v1/chat/completions \
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
HF_TOKEN="xxx" sky serve up -n gemma serve.yaml --env HF_TOKEN
```

> Notice the only change is from `sky launch` to `sky serve up`. The same YAML can be used without changes.

After the cluster is launched, we can access the model with the following command:
```bash
ENDPOINT=$(sky serve status --endpoint gemma)

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b-it",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```

Chat API is also supported:
```bash
curl -L http://$ENDPOINT/v1/chat/completions \
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

## Finetune Gemma on Your Own Data

You can also finetune Gemma on your own data with SkyPilot. We provide an example LoRA finetuning script in [scripts/finetune-lora.py](scripts/finetune-lora.py), where the model is finetuned on  the `Abirate/english_quotes` dataset.


With [finetune.yaml-lora](finetune-lora.yaml) in this directory, you can finetune the model on any cloud with a single command.

```bash
WANDB_API_KEY="xxx" HF_TOKEN="xxx" sky launch -c gemma finetune-lora.yaml --env HF_TOKEN --env WANDB_API_KEY --env BUCKET_NAME=your-bucket-name
```

You will get the following output:
```
{'loss': 1.3736, 'grad_norm': 3.4177560806274414, 'learning_rate': 0.0001, 'epoch': 1.0}
{'loss': 0.4876, 'grad_norm': 1.5821903944015503, 'learning_rate': 0.0002, 'epoch': 1.33}
{'loss': 0.6929, 'grad_norm': 2.109269380569458, 'learning_rate': 0.000175, 'epoch': 2.0}
{'loss': 0.4633, 'grad_norm': 1.5081373453140259, 'learning_rate': 0.00015000000000000001, 'epoch': 2.67}
{'loss': 0.2498, 'grad_norm': 0.8380517959594727, 'learning_rate': 0.000125, 'epoch': 3.0}
{'loss': 0.5513, 'grad_norm': 1.2455085515975952, 'learning_rate': 0.0001, 'epoch': 4.0}
{'loss': 0.4849, 'grad_norm': 1.32512366771698, 'learning_rate': 7.500000000000001e-05, 'epoch': 5.0}
{'loss': 0.1754, 'grad_norm': 1.753520131111145, 'learning_rate': 5e-05, 'epoch': 5.33}
{'loss': 0.2929, 'grad_norm': 1.5430747270584106, 'learning_rate': 2.5e-05, 'epoch': 6.0}
{'loss': 0.2797, 'grad_norm': 1.159482717514038, 'learning_rate': 0.0, 'epoch': 6.67}
{'train_runtime': 7.3703, 'train_samples_per_second': 5.427, 'train_steps_per_second': 1.357, 'train_loss': 0.505131047964096, 'epoch': 6.67}
100%|██████████| 10/10 [00:07<00:00,  1.35it/s]
```


You can also reduce cost by ~3x by using the same YAML to finetune the model on spot instances (auto-recovery enabled):
```bash
WANDB_API_KEY="xxx" HF_TOKEN="xxx" sky spot launch -n gemma finetune-lora.yaml --env HF_TOKEN --env WANDB_API_KEY --env BUCKET_NAME=your-bucket-name
```

## Serve Finetuned Gemma

After finetuning, you can serve the finetuned model:
```bash
HF_TOKEN="xxx" sky launch -c serve-gemma serve-finetuned.yaml --env BUCKET_PATH=your-bucket-name 
--env HF_TOKEN
```

You can access the finetuned model with the following command:
```bash
ENDPOINT=$(sky status --endpoint 8000 serve-gemma)

curl -L http://$ENDPOINT/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "quote",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }' | jq .
```
