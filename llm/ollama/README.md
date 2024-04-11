# Ollama: Run quantized LLMs on CPUs and GPUs
<p align="center">
  <img src="https://i.imgur.com/HfqnGVA.png" width="400">
</p>

[Ollama](https://github.com/ollama/ollama) is popular library for running LLMs on both CPUs and GPUs. 
It supports a wide range of models, including quantized versions of `llama2`, `llama2:70b`, `mistral`, `phi`, `gemma:7b` and many [more](https://ollama.com/library). 
You can use SkyPilot to run these models on CPU instances on any cloud provider, Kubernetes cluster, or even on your local machine. 
And if your instance has GPUs, Ollama will automatically use them for faster inference. 

In this example, you will run a quantized version of Llama2 on 4 CPUs with 8GB of memory, and then scale it up to more replicas with SkyServe. 

## Prerequisites
To get started, install the latest version of SkyPilot:

```bash
pip install "skypilot-nightly[all]"
```

For detailed installation instructions, please refer to the [installation guide](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

Once installed, run `sky check` to verify you have cloud access.

### [Optional] Running locally on your machine
If you do not have cloud access, you also can run this recipe on your local machine by creating a local Kubernetes cluster with `sky local up`.

Make sure you have KinD installed and Docker running with 5 or more CPUs and 10GB or more of memory allocated to the [Docker runtime](https://kind.sigs.k8s.io/docs/user/quick-start/#settings-for-docker-desktop).

To create a local Kubernetes cluster, run:

```console
sky local up
``` 

<details>
<summary>Example outputs:</summary>

```console
$ sky local up
Creating local cluster...
To view detailed progress: tail -n100 -f ~/sky_logs/sky-2024-04-09-19-14-03-599730/local_up.log
I 04-09 19:14:33 log_utils.py:79] Kubernetes is running.
I 04-09 19:15:33 log_utils.py:117] SkyPilot CPU image pulled.
I 04-09 19:15:49 log_utils.py:123] Nginx Ingress Controller installed.
⠸ Running sky check...
Local Kubernetes cluster created successfully with 16 CPUs.
`sky launch` can now run tasks locally.
Hint: To change the number of CPUs, change your docker runtime settings. See https://kind.sigs.k8s.io/docs/user/quick-start/#settings-for-docker-desktop for more info.
```
</details>

After running this, `sky check` should show that you have access to a Kubernetes cluster.

## SkyPilot YAML
To run Ollama with SkyPilot, create a YAML file with the following content:

<details>
<summary>Click to see the full recipe YAML</summary>

```yaml
envs:
  MODEL_NAME: llama2  # mistral, phi, other ollama supported models
  OLLAMA_HOST: 0.0.0.0:8888  # Host and port for Ollama to listen on

resources:
  cpus: 4+
  memory: 8+  # 8 GB+ for 7B models, 16 GB+ for 13B models, 32 GB+ for 33B models
  # accelerators: L4:1  # No GPUs necessary for Ollama, but you can use them to run inference faster
  ports: 8888

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

You can also get the full YAML [here](https://github.com/skypilot-org/skypilot/tree/master/llm/ollama/ollama.yaml).

## Serving Llama2 with a CPU instance 
Start serving Llama2 on a 4 CPU instance with the following command:

```console
sky launch ollama.yaml -c ollama --detach-run
```

Wait until the model command returns successfully.

<details>
<summary>Example outputs:</summary>

```console
...
== Optimizer ==
Target: minimizing cost
Estimated cost: $0.0 / hour

Considered resources (1 node):
-------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE            vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN   
-------------------------------------------------------------------------------------------------------
 Kubernetes   4CPU--8GB           4       8         -              kubernetes      0.00          ✔     
 AWS          c6i.xlarge          4       8         -              us-east-1       0.17                
 Azure        Standard_F4s_v2     4       8         -              eastus          0.17                
 GCP          n2-standard-4       4       16        -              us-central1-a   0.19                
 Fluidstack   rec3pUyh6pNkIjCaL   6       24        RTXA4000:1     norway_4_eu     0.64                
-------------------------------------------------------------------------------------------------------
...
```

</details>

**💡Tip:** You can further reduce costs by using the `--use-spot` flag to run on spot instances.

To launch a different model, use the `MODEL_NAME` environment variable:
    
```console
sky launch ollama.yaml -c ollama --detach-run --env MODEL_NAME=mistral
```

Ollama supports `llama2`, `llama2:70b`, `mistral`, `phi`, `gemma:7b` and many more models.
See the full list [here](https://ollama.com/library).

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

```json
{
  "id": "chatcmpl-322",
  "object": "chat.completion",
  "created": 1712015174,
  "model": "llama2",
  "system_fingerprint": "fp_ollama",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello there! *adjusts glasses* I am Assistant, your friendly and helpful AI companion. My purpose is to assist you in any way possible, from answering questions to providing information on a wide range of topics. Is there something specific you would like to know or discuss? Feel free to ask me anything!"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 29,
    "completion_tokens": 68,
    "total_tokens": 97
  }
}
```
</details>

**💡Tip:** To speed up inference, you can use GPUs by specifying the `accelerators` field in the YAML.

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

## Serving LLMs on CPUs at scale with SkyServe

After experimenting with the model, you can deploy multiple replicas of the model with autoscaling and load-balancing using SkyServe.

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
NAME    VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
ollama  1        3m 15s  READY   2/2       34.171.202.102:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP              LAUNCHED    RESOURCES       STATUS  REGION
ollama        1   1        34.69.185.170   4 mins ago  1x GCP(vCPU=4)  READY   us-central1
ollama        2   1        35.184.144.198  4 mins ago  1x GCP(vCPU=4)  READY   us-central1
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint ollama)
```

**💡Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl -L $ENDPOINT/v1/chat/completions \
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

To shut down all resources:
```console
sky serve down ollama
```

See more details in [SkyServe docs](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html).
