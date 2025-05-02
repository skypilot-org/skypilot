# Distributed DeepSeek-R1 Serving with High Throughput using SGLang and SkyPilot

<p align="center">
<img src="https://i.imgur.com/cd5C5IK.png" alt="DeepSeek-R1 on SkyPilot" style="width: 70%;">
</p>

On Jan 20, 2025, DeepSeek AI released the [DeepSeek-R1](https://github.com/deepseek-ai/DeepSeek-R1), including a family of models up to 671B parameters. 

DeepSeek-R1 naturally emerged with numerous powerful and interesting reasoning behaviors. It outperforms **state-of-the-art proprietary models** such as OpenAI-o1-mini and becomes **the first open LLM** to closely rival closed-source models like OpenAI-o1.

We use [SGLang](https://github.com/sgl-project/sglang) to serve the model distributedly with high throughput in this example.


**Note**: This example is for the original DeepSeek-R1 671B model. For smaller distilled models, please refer to [deepseek-r1-distilled](https://github.com/skypilot-org/skypilot/tree/master/llm/deepseek-r1-distilled/).

## Run 671B DeepSeek-R1 on Kubernetes or any Cloud

SkyPilot allows you to run the model distributedly with a single command, leveraging the framework [SGLang](https://github.com/sgl-project/sglang).

```bash
sky launch -c r1 llm/deepseek-r1/deepseek-r1-671B.yaml --retry-until-up
```

Below is the SkyPilot YAML configuration for DeepSeek-R1 671B, as provided in [`llm/deepseek-r1/deepseek-r1-671B.yaml`](https://github.com/skypilot-org/skypilot/blob/master/llm/deepseek-r1/deepseek-r1-671B.yaml):
```yaml
name: deepseek-r1

resources:
  accelerators: {H200:8, H100:8}
  disk_size: 1024 # Large disk for model weights
  disk_tier: best
  ports: 30000
  any_of:
    - use_spot: true
    - use_spot: false

num_nodes: 2 # Specify number of nodes to launch; requirements may vary based on accelerators

setup: |
  # Install sglang with all dependencies using uv
  uv pip install "sglang[all]>=0.4.2.post4" --find-links https://flashinfer.ai/whl/cu124/torch2.5/flashinfer

  # Set up shared memory for better performance
  sudo bash -c "echo 'vm.max_map_count=655300' >> /etc/sysctl.conf"
  sudo sysctl -p

run: |
  # Launch the server with appropriate configuration
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  # TP should be number of GPUs per node times number of nodes
  TP=$(($SKYPILOT_NUM_GPUS_PER_NODE * $SKYPILOT_NUM_NODES))

  python -m sglang.launch_server \
    --model deepseek-ai/DeepSeek-R1 \
    --tp $TP \
    --dist-init-addr ${MASTER_ADDR}:5000 \
    --nnodes ${SKYPILOT_NUM_NODES} \
    --node-rank ${SKYPILOT_NODE_RANK} \
    --trust-remote-code \
    --enable-dp-attention \
    --enable-torch-compile \
    --torch-compile-max-bs 8 \
    --host 0.0.0.0 \
    --port 30000

```

You can also adjust the `accelerators` and `num_nodes` to fit your needs. Common configurations include:

| **GPU**          | **Num Nodes** |
|------------------|---------------|
| H200:8           | 1             |
| H100:8           | 2             |
| A100-80GB:8      | 4             |
| A100:8           | 8             |

You can override `num_nodes` in the command line without modifying the YAML file. For example:

```bash
sky launch -c r1-A100 llm/deepseek-r1/deepseek-r1-671B-A100.yaml --retry-until-up --gpus A100-80GB:8 --num-nodes 4
```

> [!NOTE]
> For A100 GPUs, use [deepseek-r1-671B-A100.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/deepseek-r1/deepseek-r1-671B-A100.yaml), which includes a preprocessing step to convert the model from FP8 to BF16, as A100 does not support FP8. This conversion process takes an additional 30-40 minutes. Alternatively, you can use a pre-converted BF16 model from the Hugging Face community to skip the conversion step.
>
> Since BF16 models consume more memory, A100 deployments require twice the number of nodes compared to H100. That is, if an H100 setup requires 2 nodes, an A100-80GB setup requires 4 nodes, and an A100-40GB setup requires 8 nodes.
>
> For more configuration options, refer to the [DeepSeek SGLang Docs](https://github.com/sgl-project/sglang/blob/main/benchmark/deepseek_v3/README.md).


![Find any cheapest candidate resources](https://i.imgur.com/FpAV6Ok.png)

SkyPilot finds the cheapest candidate resources for you, and automatically fails over through different regions,
clouds, or Kubernetes clusters to find the resources to launch the model.

It may take a while (30-40 minutes) for SGLang to download the model weights, compile, and start the server.

![DeepSeek-R1 on SkyPilot](https://i.imgur.com/N51TU86.gif)

## Query the endpoint

After the initialization, you can access the model with the endpoint:
```
ENDPOINT=$(sky status --endpoint 30000 r1)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-671B",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "how many rs are in strawberry"
      }
    ]
  }' | jq .
```
 
You will get the following answer, which interestingly does not trigger any chain of thoughts.

<details>
    <summary>How many Rs are in strawberry: So, the answer is <b>3</b>. 🍓</summary>

Okay, let's figure out how many times the letter \"r\" appears in the word \"strawberry.\" First, I need to make sure I'm spelling \"strawberry\" correctly. Sometimes people might miss letters or add extra ones. Let me write it out: S-T-R-A-W-B-E-R-R-Y. Wait, is that right? Let's double-check. Strawberry is spelled S-T-R-A-W-B-E-R-R-Y. Yes, that's correct. Now, I need to go through each letter one by one and count the number of \"r\"s.\n\nStarting with the first letter: S (no), T (no), R (yes, that's one). Then A (no), W (no), B (no), E (no), R (that's two), R (that's three), Y (no). Wait, wait, hold on. Let me write out the letters with their positions to be precise.\n\nBreaking down \"strawberry\" letter by letter:\n1. S\n2. T\n3. R\n4. A\n5. W\n6. B\n7. E\n8. R\n9. R\n10. Y\n\nSo, looking at positions 3, 8, and 9: that's three \"r\"s. But wait, does that match the actual spelling? Let me confirm again. The word is strawberry. Sometimes people might think it's \"strawberry\" with two \"r\"s, but actually, according to correct spelling, it's S-T-R-A-W-B-E-R-R-Y. So after the B and E, there are two R's, right? Let me check a dictionary or maybe think of the pronunciation. Straw-ber-ry. The \"ber\" part is one R, but the correct spelling includes two R's after the E. So yes, that makes three R's in total. Hmm, but let me make sure I'm not miscounting. So positions 3, 8, 9: R, then two R's at the end before Y. That's three R's. Wait, actually, in the breakdown above, position 3 is R, then positions 8 and 9 are the two R's. So total three. Yes, that's right. So the answer should be three. Let me see if I can find any source that confirms this. Alternatively, I can write the word again and count: S T R A W B E R R Y. So R appears once at the beginning (third letter) and then twice towards the end (8th and 9th letters). So total of three times. Therefore, the correct answer is three.\n</think>\n\nThe word \"strawberry\" contains **3** instances of the letter \"r\". Here's the breakdown:\n\n1. **S**  \n2. **T**  \n3. **R** (1st \"r\")  \n4. **A**  \n5. **W**  \n6. **B**  \n7. **E**  \n8. **R** (2nd \"r\")  \n9. **R** (3rd \"r\")  \n10. **Y**  \n\nSo, the answer is **3**. 🍓

    ```console
    {"id":"01add72820794f5c884c4d5c126d2a62","object":"chat.completion","created":1739493784,"model":"deepseek-ai/DeepSeek-R1-671B","choices":[{"index":0,"message":{"role":"assistant","content":"Okay, let's figure out how many times the letter \"r\" appears in the word \"strawberry.\" First, I need to make sure I'm spelling \"strawberry\" correctly. Sometimes people might miss letters or add extra ones. Let me write it out: S-T-R-A-W-B-E-R-R-Y. Wait, is that right? Let's double-check. Strawberry is spelled S-T-R-A-W-B-E-R-R-Y. Yes, that's correct. Now, I need to go through each letter one by one and count the number of \"r\"s.\n\nStarting with the first letter: S (no), T (no), R (yes, that's one). Then A (no), W (no), B (no), E (no), R (that's two), R (that's three), Y (no). Wait, wait, hold on. Let me write out the letters with their positions to be precise.\n\nBreaking down \"strawberry\" letter by letter:\n1. S\n2. T\n3. R\n4. A\n5. W\n6. B\n7. E\n8. R\n9. R\n10. Y\n\nSo, looking at positions 3, 8, and 9: that's three \"r\"s. But wait, does that match the actual spelling? Let me confirm again. The word is strawberry. Sometimes people might think it's \"strawberry\" with two \"r\"s, but actually, according to correct spelling, it's S-T-R-A-W-B-E-R-R-Y. So after the B and E, there are two R's, right? Let me check a dictionary or maybe think of the pronunciation. Straw-ber-ry. The \"ber\" part is one R, but the correct spelling includes two R's after the E. So yes, that makes three R's in total. Hmm, but let me make sure I'm not miscounting. So positions 3, 8, 9: R, then two R's at the end before Y. That's three R's. Wait, actually, in the breakdown above, position 3 is R, then positions 8 and 9 are the two R's. So total three. Yes, that's right. So the answer should be three. Let me see if I can find any source that confirms this. Alternatively, I can write the word again and count: S T R A W B E R R Y. So R appears once at the beginning (third letter) and then twice towards the end (8th and 9th letters). So total of three times. Therefore, the correct answer is three.\n</think>\n\nThe word \"strawberry\" contains **3** instances of the letter \"r\". Here's the breakdown:\n\n1. **S**  \n2. **T**  \n3. **R** (1st \"r\")  \n4. **A**  \n5. **W**  \n6. **B**  \n7. **E**  \n8. **R** (2nd \"r\")  \n9. **R** (3rd \"r\")  \n10. **Y**  \n\nSo, the answer is **3**. 🍓","tool_calls":null},"logprobs":null,"finish_reason":"stop","matched_stop":1}],"usage":{"prompt_tokens":17,"total_tokens":688,"completion_tokens":671,"prompt_tokens_details":null}}
    ```

</details>



## Speed for Generation
You can find the generation speed in the log of the server.

Example speed for 2 H100:8 nodes on GCP with a single request (you may get better performance with gvnic enabled):
```
(head, rank=0, pid=18260) [2025-02-14 00:42:22 DP2 TP2] Decode batch. #running-req: 1, #token: 210, token usage: 0.00, gen throughput (token/s): 11.45, #queue-req: 0
(head, rank=0, pid=18260) [2025-02-14 00:42:25 DP2 TP2] Decode batch. #running-req: 1, #token: 250, token usage: 0.00, gen throughput (token/s): 11.53, #queue-req: 0
(head, rank=0, pid=18260) [2025-02-14 00:42:29 DP2 TP2] Decode batch. #running-req: 1, #token: 290, token usage: 0.00, gen throughput (token/s): 11.42, #queue-req: 0
```


## Deploy the Service with Multiple Replicas

The launching command above only starts a single replica (with 2 nodes) for the service. SkyServe helps deploy the service with multiple replicas with out-of-the-box load balancing, autoscaling and automatic recovery.
Importantly, it also enables serving on spot instances resulting in 30\% lower cost.

The only change needed is to add a service section for serving specific configuration:

```yaml
service:
  # Specifying the path to the endpoint to check the readiness of the service.
  readiness_probe:
    path: /health
    # Allow up to 1 hour for cold start
    initial_delay_seconds: 3600
  # Autoscaling from 0 to 2 replicas
  replica_policy:
    min_replicas: 0
    max_replicas: 2
```

And run the [SkyPilot YAML](https://github.com/skypilot-org/skypilot/blob/master/llm/deepseek-r1/deepseek-r1-671B.yaml) with a single command:
```bash
sky serve up -n r1-serve deepseek-r1-671B.yaml
```

