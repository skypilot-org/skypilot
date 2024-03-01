# Serving Qwen1.5 on Your Own Cloud

[Qwen1.5](https://github.com/QwenLM/Qwen1.5) is one of the top open LLMs.
As of Feb 2024, Qwen1.5-72B-Chat is ranked higher than Mixtral-8x7b-Instruct-v0.1 on the LMSYS Chatbot Arena Leaderboard.


## References
* [Qwen docs](https://qwen.readthedocs.io/en/latest/)

## Why use SkyPilot to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across multiple regions and clouds.
* Pay absolute minimum — SkyPilot picks the cheapest resources across regions and clouds. No managed solution markups.
* Scale up to multiple replicas across different locations and accelerators, all served with a single endpoint 
* Everything stays in your cloud account (your VMs & buckets)
* Completely private - no one else sees your chat history


## Running your own Qwen with SkyPilot

After [installing SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html), run your own Qwen model on vLLM with SkyPilot in 1-click:

1. Start serving Qwen 72B on a single instance with any available GPU in the list specified in [serve-72b.yaml](serve-72b.yaml) with a vLLM powered OpenAI-compatible endpoint (You can also switch to [serve-7b.yaml](serve-7b.yaml) for a smaller model):

```console
sky launch -c qwen serve-72b.yaml
```
2. Send a request to the endpoint for completion:
```bash
IP=$(sky status --ip qwen)

curl -L http://$IP:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen1.5-72B-Chat",
      "prompt": "My favorite food is",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

3. Send a request for chat completion:
```bash
curl -L http://$IP:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen1.5-72B-Chat",
      "messages": [
        {
          "role": "system",
          "content": "You are a helpful and honest chat expert."
        },
        {
          "role": "user",
          "content": "What is the best food?"
        }
      ],
      "max_tokens": 512
  }' | jq -r '.choices[0].message.content'
```

## Scale up the service with SkyServe

1. With [SkyPilot Serving](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html), a serving library built on top of SkyPilot, scaling up the Qwen service is as simple as running:
```bash
sky serve up -n qwen ./serve-72b.yaml
```
This will start the service with multiple replicas on the cheapest available locations and accelerators. SkyServe will automatically manage the replicas, monitor their health, autoscale based on load, and restart them when needed.

A single endpoint will be returned and any request sent to the endpoint will be routed to the ready replicas.

2. To check the status of the service, run:
```bash
sky serve status qwen
```
After a while, you will see the following output:
```console
Services
NAME        VERSION  UPTIME  STATUS        REPLICAS  ENDPOINT            
Qwen  1        -       READY         2/2       3.85.107.228:30002  

Service Replicas
SERVICE_NAME  ID  VERSION  IP  LAUNCHED    RESOURCES                   STATUS REGION  
Qwen          1   1        -   2 mins ago  1x Azure({'A100-80GB': 8}) READY  eastus  
Qwen          2   1        -   2 mins ago  1x GCP({'L4': 8})          READY  us-east4-a 
```
As shown, the service is now backed by 2 replicas, one on Azure and one on GCP, and the accelerator
type is chosen to be **the cheapest available one** on the clouds. That said, it maximizes the
availability of the service while minimizing the cost.

3. To access the model, we use a `curl -L` command (`-L` to follow redirect) to send the request to the endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint qwen)

curl -L http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen1.5-72B-Chat",
      "messages": [
        {
          "role": "system",
          "content": "You are a helpful and honest code assistant expert in Python."
        },
        {
          "role": "user",
          "content": "Show me the python code for quick sorting a list of integers."
        }
      ],
      "max_tokens": 512
  }' | jq -r '.choices[0].message.content'
```


## **Optional:** Accessing Code Llama with Chat GUI

It is also possible to access the Code Llama service with a GUI using [FastChat](https://github.com/lm-sys/FastChat).

1. Start the chat web UI:
```bash
sky launch -c qwen-gui ./gui.yaml --env ENDPOINT=$(sky serve status --endpoint qwen)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```

Note that you may get better results to use a higher temperature and top_p value.

