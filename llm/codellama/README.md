# Serving Code Llama with vLLM

[Code Llama](https://github.com/facebookresearch/codellama) is a code-specialized version of Llama 2 that was created by further training Llama 2 on its code-specific datasets, sampling more data from that same dataset for longer. On Jan 29th, 2024, Meta releases the Code Llama 70B, the largest and best-performing model in the Code Llama family.

## References
* [Llama-2 Example](../../llm/llama-2/)
* [Code Llama release](https://ai.meta.com/blog/code-llama-large-language-model-coding/)
* [Code Llama paper](https://arxiv.org/abs/2308.12950)


## Why use SkyPilot/SkyServe to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across multiple regions and clouds.
* Enable scaling up to multiple replicas across different locations and accelerators with a single endpoint exposed.
* Everything stays in your cloud account (your VMs & buckets)
* No one else sees your chat history
* Pay absolute minimum — no managed solution markups
![Code Llama GUI](https://imgur.com/Dor1MoE.gif)


## Running your own Code Llama with SkyPilot

After [installing SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html), run your own Code Llama on vLLM with SkyPilot in 1-click:

1. Start servingCode Llama on a single instance with any available GPU in the list specified in [endpoint.yaml](endpoint.yaml) with a vLLM powered OpenAI-compatible endpoint:
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
      "model": "codellama/CodeLlama-70b-Python-hf",
      "prompt": "def quick_sort(a: List[int]):",
      "max_tokens": 256
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

1. With [SkyServe](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html), scaling up the Code Llama service is as simple as running:
```bash
sky serve up -n code-llama ./endpoint.yaml
```
This will start the service with multiple replicas on the cheapest available locations and accelerators. SkyServe will automatically manage the replicas, monitor their health, and restart them when needed.

A single endpoint will be returned and any request sent to the endpoint will be routed to the healthy replicas.

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
code-llama    1   1        -   2 mins ago  1x Azure({'A100-80GB': 2})) READY  eastus  
code-llama    2   1        -   2 mins ago  1x GCP({'L4': 8}))          READY  us-east4-a 
```
As shown, the service is now backed by 2 replicas, one on Azure and one on GCP, and the accelerator
type is chosen to be **the cheapest and available one** on the clouds. That said, it maximizes the
availability of the service while minimizing the cost.

3. To access the model, we use the same curl command to send the request to the endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint code-llama)

curl -L http://$ENDPOINT/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "codellama/CodeLlama-70b-Python-hf",
      "prompt": "def quick_sort(a: List[int]):",
      "max_tokens": 256
  }' | jq -r '.choices[0].text'
```

## **Optional:** Accessing Code Llama with Chat API

We can also chat with Code Llama using the same endpoint by using the `codellama/CodeLlama-70b-Instruct-hf` model.
To test it, we first replace the model in the [endpoint.yaml](endpoint.yaml) with the instruction-tuned model:
```bash
sed -i 's/codellama\/CodeLlama-70b-Python-hf/codellama\/CodeLlama-70b-Instruct-hf/g' endpoint.yaml
```
Then, we launch the service with the new endpoint:
```bash
sky serve up -n code-llama ./endpoint.yaml
```
Or, we can update the existing service:
```bash
sky serve update code-llama ./endpoint.yaml
```

```bash
ENDPOINT=$(sky serve status --endpoint code-llama-instruct)

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
      "max_tokens": 256,
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

Alternatively, you could access the model through python with OpenAI's API (see [complete.py](complete.py)):
```bash
python complete.py
```

## **Optional:** Accessing Code Llama with Chat GUI

It is also possible to access the Code Llama service with a GUI. To do so, 
1. We can start the chat web UI:
```bash
sky launch -c code-llama-gui ./gui.yaml --env ENDPOINT=$(sky serve status --endpoint code-llama)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```

Please check the GIF at the top of this page for a demo of the web GUI.
