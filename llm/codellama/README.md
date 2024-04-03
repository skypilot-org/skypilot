<!-- $REMOVE -->
# Code Llama: Serve Your Private Code Model with API, Chat, and VSCode Access
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Code Llama: Serve Your Private Code Model -->

[Code Llama](https://github.com/facebookresearch/codellama) is a code-specialized version of Llama 2 that was created by further training Llama 2 on its code-specific datasets, sampling more data from that same dataset for longer. On Jan 29th, 2024, Meta released the Code Llama 70B, the largest and best-performing model in the Code Llama family.

The followings are the demos of Code Llama 70B hosted by SkyPilot Serve (aka SkyServe) (see more details about the setup in later sections):

## Demos
<figure>
<center>
<img src="https://imgur.com/fguAmP0.gif" width="60%" title="Coding Assistant: Connect to hosted Code Llama with Tabby in VScode" />

<figcaption>Coding Assistant: Connect to hosted Code Llama with Tabby in VScode</figcaption>
</figure>

<figure>
<center>
<img src="https://imgur.com/Dor1MoE.gif" width="60%" title="Chat: Connect to hosted Code Llama with FastChat" />

<figcaption>Chat: Connect to hosted Code Llama with FastChat</figcaption>
</figure>

## References
* [Llama-2 Example](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-2/)
* [Code Llama release](https://ai.meta.com/blog/code-llama-large-language-model-coding/)
* [Code Llama paper](https://arxiv.org/abs/2308.12950)

## Why use SkyPilot to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across multiple regions and clouds.
* Pay absolute minimum — SkyPilot picks the cheapest resources across regions and clouds. No managed solution markups.
* Scale up to multiple replicas across different locations and accelerators, all served with a single endpoint 
* Everything stays in your cloud account (your VMs & buckets)
* Completely private - no one else sees your chat history


## Running your own Code Llama with SkyPilot

After [installing SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html), run your own Code Llama on vLLM with SkyPilot in 1-click:

1. Start serving Code Llama 70B on a single instance with any available GPU in the list specified in [endpoint.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/codellama/endpoint.yaml) with a vLLM powered OpenAI-compatible endpoint:
```console
sky launch -c code-llama -s endpoint.yaml

----------------------------------------------------------------------------------------------------------
CLOUD   INSTANCE                    vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN   
----------------------------------------------------------------------------------------------------------
 Azure   Standard_NC48ads_A100_v4    48      440       A100-80GB:2    eastus          7.35          ✔     
 GCP     g2-standard-96              96      384       L4:8           us-east4-a      7.98                
 GCP     a2-ultragpu-2g              24      340       A100-80GB:2    us-central1-a   10.06               
 Azure   Standard_NC96ads_A100_v4    96      880       A100-80GB:4    eastus          14.69               
 GCP     a2-highgpu-4g               48      340       A100:4         us-central1-a   14.69               
 AWS     g5.48xlarge                 192     768       A10G:8         us-east-1       16.29               
 GCP     a2-ultragpu-4g              48      680       A100-80GB:4    us-central1-a   20.11               
 Azure   Standard_ND96asr_v4         96      900       A100:8         eastus          27.20               
 GCP     a2-highgpu-8g               96      680       A100:8         us-central1-a   29.39               
 AWS     p4d.24xlarge                96      1152      A100:8         us-east-1       32.77               
 Azure   Standard_ND96amsr_A100_v4   96      1924      A100-80GB:8    eastus          32.77               
 GCP     a2-ultragpu-8g              96      1360      A100-80GB:8    us-central1-a   40.22               
 AWS     p4de.24xlarge               96      1152      A100-80GB:8    us-east-1       40.97               
----------------------------------------------------------------------------------------------------------

Launching a cluster 'code-llama'. Proceed? [Y/n]: 
```
2. Send a request to the endpoint for code completion:
```bash
IP=$(sky status --ip code-llama)

curl -L http://$IP:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "codellama/CodeLlama-70b-Instruct-hf",
      "prompt": "def quick_sort(a: List[int]):",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

This returns the following completion:
```python
    if len(a) <= 1:
        return a
    pivot = a.pop(len(a)//2)
    b = []
    c = []
    for i in a:
        if i > pivot:
            b.append(i)
        else:
            c.append(i)
    b = quick_sort(b)
    c = quick_sort(c)
    res = []
    res.extend(c)
    res.append(pivot)
    res.extend(b)
    return res
```

## Scale up the service with SkyServe

1. With [SkyServe](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html), a serving library built on top of SkyPilot, scaling up the Code Llama service is as simple as running:
```bash
sky serve up -n code-llama ./endpoint.yaml
```
This will start the service with multiple replicas on the cheapest available locations and accelerators. SkyServe will automatically manage the replicas, monitor their health, autoscale based on load, and restart them when needed.

A single endpoint will be returned and any request sent to the endpoint will be routed to the ready replicas.

2. To check the status of the service, run:
```bash
sky serve status code-llama
```
After a while, you will see the following output:
```console
Services
NAME        VERSION  UPTIME  STATUS        REPLICAS  ENDPOINT            
code-llama  1        -       READY         2/2       3.85.107.228:30002  

Service Replicas
SERVICE_NAME  ID  VERSION  IP  LAUNCHED    RESOURCES                   STATUS REGION  
code-llama    1   1        -   2 mins ago  1x Azure({'A100-80GB': 2}) READY  eastus  
code-llama    2   1        -   2 mins ago  1x GCP({'L4': 8})          READY  us-east4-a 
```
As shown, the service is now backed by 2 replicas, one on Azure and one on GCP, and the accelerator
type is chosen to be **the cheapest available one** on the clouds. That said, it maximizes the
availability of the service while minimizing the cost.

3. To access the model, we use the same curl command to send the request to the endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint code-llama)

curl -L http://$ENDPOINT/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "codellama/CodeLlama-70b-Instruct-hf",
      "prompt": "def quick_sort(a: List[int]):",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

## **Optional:** Accessing Code Llama with Chat API

We can also access the Code Llama service with the openAI Chat API.
```bash
ENDPOINT=$(sky serve status --endpoint code-llama)

curl -L http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "codellama/CodeLlama-70b-Instruct-hf",
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

You can see something similar as below:
````````
```python
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)

# Example usage:
numbers = [10, 2, 44, 15, 30, 11, 50]
sorted_numbers = quicksort(numbers)
print(sorted_numbers)
```

This code defines a function `quicksort` that takes a list of integers as input. It divides the list into three parts based on the pivot element, which is the middle element of the list. It then recursively sorts the left and right partitions and combines them with the middle partition.
````````

Alternatively, we could access the model through python with OpenAI's API (see [complete.py](https://github.com/skypilot-org/skypilot/tree/master/llm/codellama/complete.py)):
```bash
python complete.py
```

## **Optional:** Accessing Code Llama with Chat GUI

It is also possible to access the Code Llama service with a GUI using [FastChat](https://github.com/lm-sys/FastChat). Please check the [demo](#demos).

1. Start the chat web UI:
```bash
sky launch -c code-llama-gui ./gui.yaml --env ENDPOINT=$(sky serve status --endpoint code-llama)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```

Note that you may get better results to use a higher temperature and top_p value.


## **Optional:** Using Code Llama as Coding Assistant in VScode

[Tabby](https://tabby.tabbyml.com/) is an open-source, self-hosted AI coding assistant. It allows you to connect
to your own AI models and use them as a coding assistant in VScode. Please check the [demo](#demos) at the top.

To start a Tabby server that connects to the Code Llama service, run:
```bash
sky launch -c tabby ./tabby.yaml --env ENDPOINT=$(sky serve status --endpoint code-llama)
```

To get the endpoint for Tabby server, run:
```bash
IP=$(sky status --ip tabby)
echo Endpoint: http://$IP:8080
```

Then, you can connect to the Tabby server from VScode by installing the [Tabby extension](https://marketplace.visualstudio.com/items?itemName=TabbyML.vscode-tabby) and configuring the API Endpoint under Tabby settings.

> Note that Code Llama 70B does not have the full infiling functionality [[1](https://huggingface.co/codellama/CodeLlama-70b-Instruct-hf)], so the performance of Tabby with Code Llama may be limited.
> 
> To get infiling functionality, you can use the smaller Code Llama models, e.g., Code Llama [7B](https://huggingface.co/codellama/CodeLlama-13B-Instruct-hf) and [13B](https://huggingface.co/codellama/CodeLlama-13B-Instruct-hf), and replace `prompt_template` with `"<｜fim▁begin｜>{prefix}<｜fim▁hole｜>{suffix}<｜fim▁end｜>"` in the [yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/codellama/tabby.yaml) or the command above.
> 
> For better performance, we recommend using Tabby with the recommended models in the [Tabby documentation](https://tabby.tabbyml.com/docs/models/) and our [Tabby example](https://github.com/skypilot-org/skypilot/tree/master/llm/tabby).

