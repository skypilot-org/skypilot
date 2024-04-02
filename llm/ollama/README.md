# Ollama: Run quantized LLMs on CPUs

<!-- TODO: add image -->

[Ollama](https://github.com/ollama/ollama) is popular library for running LLMs on both CPUs and GPUs. 
It supports a wide range of models, including quantized versions of `llama2`, `llama2:70b`, `mistral`, `phi`, `gemma:7b` and many [more](https://ollama.com/library). 
You can use SkyPilot to run these models on CPU instances on any cloud provider, Kubernetes cluster, or even on your local machine. 

In this recipe, you will run a quantized version of Llama2 on 4 CPUs with 8GB of memory, and then scale it up to more replicas with SkyServe. 

## Prerequisites
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
To run Ollama with SkyPilot, create a YAML file with the following content:

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml
resources:
  cpus: 4+  # No GPUs necessary for Ollama
  memory: 8+  # 8 GB+ for 7B models, 16 GB+ for 13B models, 32 GB+ for 33B models
  ports: 8888

envs:
  MODEL_NAME: llama2  # mistral, phi, other ollama supported models
  OLLAMA_HOST: 0.0.0.0:8888  # Host and port for Ollama to listen on

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
  echo "Serving model $MODEL_NAME"
  ollama serve
```
</details>

You can also get the full YAML file [here](https://github.com/skypilot-org/skypilot/tree/master/llm/ollama/ollama.yaml).

### Serving Llama2 with a CPU instance 
Start serving Llama2 on a 4 CPU instance with the following command:

```console
sky launch ollama.yaml -c ollama --detach-run
```

Wait until the model command returns successfully.

<details>
<summary>Example outputs:</summary>

```console
...
I 04-01 16:35:46 optimizer.py:690] == Optimizer ==
I 04-01 16:35:46 optimizer.py:701] Target: minimizing cost
I 04-01 16:35:46 optimizer.py:713] Estimated cost: $0.0 / hour
I 04-01 16:35:46 optimizer.py:713] 
I 04-01 16:35:46 optimizer.py:836] Considered resources (1 node):
I 04-01 16:35:46 optimizer.py:906] -------------------------------------------------------------------------------------------------------
I 04-01 16:35:46 optimizer.py:906]  CLOUD        INSTANCE            vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN   
I 04-01 16:35:46 optimizer.py:906] -------------------------------------------------------------------------------------------------------
I 04-01 16:35:46 optimizer.py:906]  Kubernetes   4CPU--8GB           4       8         -              kubernetes      0.00          ✔     
I 04-01 16:35:46 optimizer.py:906]  AWS          c6i.xlarge          4       8         -              us-east-1       0.17                
I 04-01 16:35:46 optimizer.py:906]  Azure        Standard_F4s_v2     4       8         -              eastus          0.17                
I 04-01 16:35:46 optimizer.py:906]  GCP          n2-standard-4       4       16        -              us-central1-a   0.19                
I 04-01 16:35:46 optimizer.py:906]  Fluidstack   rec3pUyh6pNkIjCaL   6       24        RTXA4000:1     norway_4_eu     0.64                
I 04-01 16:35:46 optimizer.py:906] -------------------------------------------------------------------------------------------------------
...
```

</details>

To launch a different model, use the `MODEL_NAME` environment variable. 
Ollama supports `llama2`, `llama2:70b`, `mistral`, `phi`, `gemma:7b` and many more models.
See the full list [here](https://ollama.com/library).:
    
```console
sky launch ollama.yaml -c ollama --env MODEL_NAME=mistral
```

Once the `sky launch` command returns successfully, you can interact with the model via
- Standard OpenAPI-compatible endpoints (e.g., `/v1/chat/completions`)
- [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8888 ollama)
curl $ENDPOINT/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
       "model": "llama2",
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

<details>
<summary>Example curl response:</summary>

```console
$ ENDPOINT=$(sky status --endpoint 8888 ollama)
$ curl $ENDPOINT/v1/chat/completions \
 -H "Content-Type: application/json" \
 -d '{
       "model": "llama2",
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
{"id":"chatcmpl-322","object":"chat.completion","created":1712015174,"model":"llama2","system_fingerprint":"fp_ollama","choices":[{"index":0,"message":{"role":"assistant","content":"Hello there! *adjusts glasses* I am Assistant, your friendly and helpful AI companion. My purpose is to assist you in any way possible, from answering questions to providing information on a wide range of topics. Is there something specific you would like to know or discuss? Feel free to ask me anything!"},"finish_reason":"stop"}],"usage":{"prompt_tokens":29,"completion_tokens":68,"total_tokens":97}}
```
</details>


To stop the instance:
```console
sky stop ollama
```

To shut down all resources:
```console
sky down ollama
```

If you are using a local Kubernetes cluster created with `sky local up`, shut it down with:
```console
sky local down
```

## Serving LLMs with CPUs: scaling up with SkyServe

After experimenting with the model, you can deploy the model with autoscaling and load-balancing using SkyServe.

With no change to the YAML, launch a fully managed service on your infra:
```console
sky serve up ollama.yaml -n ollama
```

Wait until the service is ready:
```console
watch -n10 sky serve status ollama
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME  VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
dbrx  1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                       STATUS  REGION
dbrx          1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
dbrx          2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'A100-80GB': 4})  READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint dbrx)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl -L $ENDPOINT/v1/chat/completions \
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

To shut down all resources:
```console
sky serve down dbrx
```

See more details in [SkyServe docs](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html).
