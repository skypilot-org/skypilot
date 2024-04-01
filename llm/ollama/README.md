# Ollama: Run LLMs on CPUs
![image](https://github.com/skypilot-org/skypilot/assets/6753189/e452c39e-b5ef-4cb2-ab48-053f9e6f67b7)


## Prerequsites
To get started, install the latest version of SkyPilot:

```bash
pip install "skypilot-nightly[all]"
```

For detailed installation instructions, please refer to the [installation guide](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

Once installed, run `sky check` to verify you have cloud access.

💡Tip: if you do not have any cloud access, you can run this recipe on your local machine
by creating a local Kubernetes cluster with `sky local up`. 

## Running Ollama with SkyPilot

### SkyPilot YAML
## SkyPilot YAML

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml
resources:
  cpus: 4+  # No GPUs necessary for Ollama!
  memory: 8+  # 8 GB+ for 7B models, 16 GB+ for 13B models, 32 GB+ for 33B models 
  ports: 8888

envs:
  MODEL_NAME: llama2 # mistral, phi, other ollama supported models
  OLLAMA_HOST: 0.0.0.0:8888

setup: |
  # Install Ollama
  if [ "$(uname -m)" == "aarch64" ]; then
    # For apple silicon support
    sudo curl -L https://ollama.com/download/ollama-linux-arm64 -o /usr/bin/ollama
  else
    sudo curl -L https://ollama.com/download/ollama-linux-amd64 -o /usr/bin/ollama
  fi
  sudo chmod +x /usr/bin/ollama
  
  # Start `ollama serve` and capture PID to kill it after pull is done
  ollama serve &
  OLLAMA_PID=$!
  
  # Wait for ollama to be ready
  IS_READY=false
  for i in {1..20};
    do ollama list && IS_READY=true && break;
    sleep 5;
  done
  if [ "$IS_READY" = false ]; then
      echo "Ollama was not ready after 100 seconds. Exiting."
      exit 1
  fi
  
  # Pull the model
  ollama pull $MODEL_NAME
  echo "Model $MODEL_NAME pulled successfully."
  
  # Kill `ollama serve` after pull is done
  kill $OLLAMA_PID

run: |
  # Run `ollama serve` in the foreground
  echo "Serve model $MODEL_NAME"
  ollama serve

```
</details>

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/tree/master/llm/dbrx/dbrx.yaml).
