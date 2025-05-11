# Serving Qwen3/Qwen2 on Your Own Kubernetes or Cloud

[Qwen2](https://github.com/QwenLM/Qwen2) is one of the top open LLMs.
As of Jun 2024, Qwen1.5-110B-Chat is ranked higher than GPT-4-0613 on the [LMSYS Chatbot Arena Leaderboard](https://chat.lmsys.org/?leaderboard).

**Update (Apr 28, 2025) -** SkyPilot now supports the [**Qwen3**](https://qwenlm.github.io/blog/qwen3/) model! 

📰 **Update (Sep 18, 2024) -** SkyPilot now supports the [**Qwen2.5**](https://qwenlm.github.io/blog/qwen2.5/) model! 

📰 **Update (Jun 6, 2024) -** SkyPilot now also supports the [**Qwen2**](https://qwenlm.github.io/blog/qwen2/) model! It further improves the competitive model, Qwen1.5.

📰 **Update (April 26, 2024) -** SkyPilot now also supports the [**Qwen1.5-110B**](https://qwenlm.github.io/blog/qwen1.5-110b/) model! It performs competitively with Llama-3-70B across a [series of evaluations](https://qwenlm.github.io/blog/qwen1.5-110b/#model-quality). Use [qwen15-110b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/qwen/qwen15-110b.yaml) to serve the 110B model.

## One command to start a Qwen3

```bash
sky launch -c qwen qwen3-235b.yaml
```

<p align="center">
    <img src="https://i.imgur.com/d7tEhAl.gif" alt="qwen" width="600"/>
</p>

## References
* [Qwen docs](https://qwen.readthedocs.io/en/latest/)

## Why use SkyPilot to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across Kubernetes clusters and multiple regions/clouds.
* Pay absolute minimum — SkyPilot picks the cheapest resources across Kubernetes clusters and regions/clouds. No managed solution markups.
* Scale up to multiple replicas across different locations and accelerators, all served with a single endpoint 
* Everything stays in your Kubernetes or cloud account (your VMs & buckets)
* Completely private - no one else sees your chat history


## Running your own Qwen with SkyPilot

After [installing SkyPilot](https://docs.skypilot.co/en/latest/getting-started/installation.html), run your own Qwen model on vLLM with SkyPilot in 1-click:

1. Start serving Qwen 110B on a single instance with any available GPU in the list specified in [qwen15-110b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/qwen/qwen15-110b.yaml) with a vLLM powered OpenAI-compatible endpoint (You can also switch to [qwen25-72b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/qwen/qwen25-72b.yaml) or [qwen25-7b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/qwen/qwen25-7b.yaml) for a smaller model):

```console
sky launch -c qwen qwen3-235b.yaml
```
2. Send a request to the endpoint for completion:
```bash
ENDPOINT=$(sky status --endpoint 8000 qwen)

curl http://$ENDPOINT/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen3-235B-A22B-FP8",
      "prompt": "My favorite food is",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

3. Send a request for chat completion:
```bash
curl http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen3-235B-A22B-FP8",
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
<details>
<summary>Qwen3 output</summary>

```
The concept of "the best food" is highly subjective and depends on personal preferences, cultural background, dietary needs, and even mood! For example:

- **Some crave comfort foods** like macaroni and cheese, ramen, or dumplings.  
- **Others prioritize health** and might highlight dishes like quinoa bowls, grilled salmon, or fresh salads.  
- **Global favorites** often include pizza, sushi, tacos, or curry.  
- **Unique or adventurous eaters** might argue for dishes like insects, fermented foods, or molecular gastronomy creations.  

Could you clarify what you mean by "best"? For instance:  
- Are you asking about taste, health benefits, cultural significance, or something else?  
- Are you looking for a specific dish, ingredient, or cuisine?  

This helps me tailor a more meaningful answer! 😊
```

</details>

## Running Multimodal Qwen2-VL


1. Start serving Qwen2-VL:

```console
sky launch -c qwen2-vl qwen2-vl-7b.yaml
```
2. Send a multimodalrequest to the endpoint for completion:
```bash
ENDPOINT=$(sky status --endpoint 8000 qwen2-vl)

curl http://$ENDPOINT/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer token' \
    --data '{
        "model": "Qwen/Qwen2-VL-7B-Instruct",
        "messages": [
        {
            "role": "user",
            "content": [
                {"type" : "text", "text": "Covert this logo to ASCII art"},
                {"type": "image_url", "image_url": {"url": "https://pbs.twimg.com/profile_images/1584596138635632640/HWexMoH5_400x400.jpg"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```

## Scale up the service with SkyServe

1. With [SkyPilot Serving](https://docs.skypilot.co/en/latest/serving/sky-serve.html), a serving library built on top of SkyPilot, scaling up the Qwen service is as simple as running:
```bash
sky serve up -n qwen ./qwen25-72b.yaml
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
NAME  VERSION  UPTIME  STATUS        REPLICAS  ENDPOINT            
Qwen  1        -       READY         2/2       3.85.107.228:30002  

Service Replicas
SERVICE_NAME  ID  VERSION  ENDPOINT  LAUNCHED    RESOURCES                  STATUS REGION  
Qwen          1   1        -         2 mins ago  1x Azure({'A100-80GB': 8}) READY  eastus  
Qwen          2   1        -         2 mins ago  1x GCP({'L4': 8})          READY  us-east4-a 
```
As shown, the service is now backed by 2 replicas, one on Azure and one on GCP, and the accelerator
type is chosen to be **the cheapest available one** on the clouds. That said, it maximizes the
availability of the service while minimizing the cost.

3. To access the model, we use a `curl` command to send the request to the endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint qwen)

curl http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen2.5-72B-Instruct",
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


## **Optional:** Accessing Qwen with Chat GUI

It is also possible to access the Qwen service with a GUI using [vLLM](https://github.com/vllm-project/vllm).

1. Start the chat web UI (change the `--env` flag to the model you are running):
```bash
sky launch -c qwen-gui ./gui.yaml --env MODEL_NAME='Qwen/Qwen2.5-72B-Instruct' --env ENDPOINT=$(sky serve status --endpoint qwen)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```

