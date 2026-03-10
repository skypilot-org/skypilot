# Run and Serve DeepSeek-R1 Distilled Models with SkyPilot and vLLM

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

<p align="center">
<img src="https://i.imgur.com/6umSuKw.png" alt="DeepSeek-R1 on SkyPilot" style="width: 70%;">
</p>

On Jan 20, 2025, DeepSeek AI released the [DeepSeek-R1](https://github.com/deepseek-ai/DeepSeek-R1), including a family of models up to 671B parameters.

DeepSeek-R1 naturally emerged with numerous powerful and interesting reasoning behaviors. It outperforms **state-of-the-art proprietary models** such as OpenAI-o1-mini and becomes **the first time** an open LLM closely rivals like OpenAI-o1.

This guide walks through how to run and host DeepSeek-R1 models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public Clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)).

Skypilot supports a variety of LLM frameworks and models. In this guide, we use [vLLM](https://github.com/vllm-project/vllm), an open-source library for fast LLM inference and serving, as an example.

**New**: We added a new SkyPilot YAML for running [DeepSeek-R1 671B with SGLang](https://github.com/skypilot-org/skypilot/tree/master/llm/deepseek-r1).

## Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run DeepSeek-R1 on:

**If your local machine/cluster has GPU**: you can run SkyPilot [directly on existing machines](https://docs.skypilot.co/en/latest/reservations/existing-machines.html) with

```bash
sky local up
```

**If you want to use Clouds** (15+ clouds are supported):

```bash
sky check
```
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


## Step 1: Run it with SkyPilot

Now it's time to run deepseek with SkyPilot. The instruction can be dependent on your existing hardware.

8B:
```
sky launch deepseek-r1-vllm.yaml \
  -c deepseek \
  --env MODEL_NAME=deepseek-ai/DeepSeek-R1-Distill-Llama-8B \
  --gpus L4:1
```

70B:
```
sky launch deepseek-r1-vllm.yaml \
  -c deepseek \
  --env MODEL_NAME=deepseek-ai/DeepSeek-R1-Distill-Llama-70B \
  --gpus A100-80GB:2
```

replace the command with the model and the GPU that you wish to use. You may run `sky gpus list` to know what GPU that you have access to. As a reference, here is the model-GPU compatibility matrix:

| **GPU**         	| **DeepSeek-R1-Distill-Qwen-7B**  | **DeepSeek-R1-Distill-Llama-70B** 	| **DeepSeek-R1**  	|
|-----------------	|------------------------------	|------------------------	|------------------------------	|
| **L4:1**        	| ✅, with `--max-model-len 4096` 	| ❌                      	| ❌                            	|
| **L4:8**        	| ✅                            	| ❌                      	| ❌                            	|
| **A100:8**      	| ✅                            	| ✅                      	| ❌                            	|
| **A100-80GB:12** 	| ✅                            	| ✅                      	| ✅, with `--max-model-len 4096` 	|

## Step 2: Get Results
Get a single endpoint that load-balances across replicas:

```
ENDPOINT=$(sky status --ip deepseek)
```

Query the endpoint in a terminal:
8B:
```
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
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
  }' | jq .
```

70B:
```
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
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
</details>

You will get both the chain of thoughts within `<think>` tags and the final results.

<details>
    <summary>Who are you? I'm DeepSeek-R1.</summary>

Greetings! I'm DeepSeek-R1, an artificial intelligence assistant created by DeepSeek. I'm at your service and would be delighted to assist you with any inquiries or tasks you may have.

```console
{
  "id": "chatcmpl-507f467863344f31b98d8bf36b9a3c1c",
  "object": "chat.completion",
  "created": 1737503962,
  "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\n\n</think>\n\nGreetings! I'm DeepSeek-R1, an artificial intelligence assistant created by DeepSeek. I'm at your service and would be delighted to assist you with any inquiries or tasks you may have.",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 13,
    "total_tokens": 57,
    "completion_tokens": 44,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null
}
```
</details>


<details>
    <summary>How many Rs are in strawberry: There are 3 Rs in strawberry.</summary>

\<think\>
Okay, so I need to figure out how many times the letter 'R' appears in the word "strawberry." Hmm, let me think about this step by step. First, I should probably write out the word to visualize it better. The word is S-T-R-A-W-B-E-R-R-Y. Wait, is that right? Let me double-check. S-T-R-A-W-B-E-R-R-Y, yes, that's how it's spelled.

Now, I need to go through each letter one by one and count the Rs. Starting with the first letter, it's an S. Not an R, so move on. The second letter is T, still not an R. The third letter is R. Okay, that's the first R. I'll note that down.

Next letters: A, W, B, E. None of those are Rs. So far, only one R. Then comes R again after E, right? So that's the second R. But wait, I think there's another R after that. Let me make sure. After the second R, there's another R, making it the third R. Wait, no, let me check the spelling again. It's S-T-R-A-W-B-E-R-R-Y. So after E, it's R, then another R, so that's two Rs there. So total, how many?

Let me recount: first R is the third letter, then after E, there's another R, making it two in total, or three? Wait, no. Let me look at each position:

1. S
2. T
3. R (1st R)
4. A
5. W
6. B
7. E
8. R (2nd R)
9. R (3rd R)
10. Y

Wait, so after E, there are two Rs in a row, which would make it the 8th and 9th letters. So that's two more Rs after the first one. So total, it's three Rs? Or is that correct? Let me make sure I'm not overcounting. Let's write it out:

S T R A W B E R R Y

So, positions:

1: S

2: T

3: R (1)

4: A

5: W

6: B

7: E

8: R (2)

9: R (3)

10: Y

So that's three Rs. Wait, but when I think about the word "strawberry," I thought it had two Rs, but maybe it's three. Wait, maybe I'm wrong. Let me check a dictionary or something, but since I can't do that, I'll have to rely on my memory. Hmm, maybe I was mistaken earlier. Let me think again. Strawberries have a double R, I believe. But in the spelling, is it R-A-W-B-E-R-R-Y? So after the E, it's R-R-Y. So that's two Rs at the end. Plus the one after the T, so that's three Rs total.

Wait, no. Let me think about how the word is pronounced. It's "straw" plus "berry," right? So "straw" has one R, and "berry" has two Rs? No, "berry" only has one R. Wait, no, "berry" is B-E-R-R-Y, so there are two Rs there. So when you put it together, "strawberry" would have the R from "straw" and two Rs from "berry," making three Rs. Hmm, but I'm not sure. Some people might think it's only two Rs, but based on the spelling, it's three.

Wait, no, actually, let me break it down. The word is S-T-R-A-W-B-E-R-R-Y. So after the T, there's an R, then later after the B and E, there's another R, and then another R before Y. So that's three Rs. So the answer should be three. But I'm a bit confused because sometimes people might miscount, thinking it's two. But according to the spelling, it's three. I think that's correct.
\</think\>

The word "strawberry" contains three Rs.

Step-by-step breakdown:
- The first R is the third letter.
- The second R is the eighth letter.
- The third R is the ninth letter.

Counting each occurrence: 1 (position 3), 2 (position 8), and 3 (position 9).

Answer: There are 3 Rs in "strawberry."

```console
{
  "id": "chatcmpl-d532bd1c1738493ab9c8c906550044bf",
  "object": "chat.completion",
  "created": 1737507945,
  "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\nOkay, so I need to figure out how many times the letter 'R' appears in the word \"strawberry.\" Hmm, let me think about this step by step. First, I should probably write out the word to visualize it better. The word is S-T-R-A-W-B-E-R-R-Y. Wait, is that right? Let me double-check. S-T-R-A-W-B-E-R-R-Y, yes, that's how it's spelled.\n\nNow, I need to go through each letter one by one and count the Rs. Starting with the first letter, it's an S. Not an R, so move on. The second letter is T, still not an R. The third letter is R. Okay, that's the first R. I'll note that down.\n\nNext letters: A, W, B, E. None of those are Rs. So far, only one R. Then comes R again after E, right? So that's the second R. But wait, I think there's another R after that. Let me make sure. After the second R, there's another R, making it the third R. Wait, no, let me check the spelling again. It's S-T-R-A-W-B-E-R-R-Y. So after E, it's R, then another R, so that's two Rs there. So total, how many?\n\nLet me recount: first R is the third letter, then after E, there's another R, making it two in total, or three? Wait, no. Let me look at each position:\n\n1. S\n2. T\n3. R (1st R)\n4. A\n5. W\n6. B\n7. E\n8. R (2nd R)\n9. R (3rd R)\n10. Y\n\nWait, so after E, there are two Rs in a row, which would make it the 8th and 9th letters. So that's two more Rs after the first one. So total, it's three Rs? Or is that correct? Let me make sure I'm not overcounting. Let's write it out:\n\nS T R A W B E R R Y\n\nSo, positions:\n\n1: S\n\n2: T\n\n3: R (1)\n\n4: A\n\n5: W\n\n6: B\n\n7: E\n\n8: R (2)\n\n9: R (3)\n\n10: Y\n\nSo that's three Rs. Wait, but when I think about the word \"strawberry,\" I thought it had two Rs, but maybe it's three. Wait, maybe I'm wrong. Let me check a dictionary or something, but since I can't do that, I'll have to rely on my memory. Hmm, maybe I was mistaken earlier. Let me think again. Strawberries have a double R, I believe. But in the spelling, is it R-A-W-B-E-R-R-Y? So after the E, it's R-R-Y. So that's two Rs at the end. Plus the one after the T, so that's three Rs total.\n\nWait, no. Let me think about how the word is pronounced. It's \"straw\" plus \"berry,\" right? So \"straw\" has one R, and \"berry\" has two Rs? No, \"berry\" only has one R. Wait, no, \"berry\" is B-E-R-R-Y, so there are two Rs there. So when you put it together, \"strawberry\" would have the R from \"straw\" and two Rs from \"berry,\" making three Rs. Hmm, but I'm not sure. Some people might think it's only two Rs, but based on the spelling, it's three.\n\nWait, no, actually, let me break it down. The word is S-T-R-A-W-B-E-R-R-Y. So after the T, there's an R, then later after the B and E, there's another R, and then another R before Y. So that's three Rs. So the answer should be three. But I'm a bit confused because sometimes people might miscount, thinking it's two. But according to the spelling, it's three. I think that's correct.\n</think>\n\nThe word \"strawberry\" contains three Rs. \n\nStep-by-step breakdown:\n- The first R is the third letter.\n- The second R is the eighth letter.\n- The third R is the ninth letter.\n\nCounting each occurrence: 1 (position 3), 2 (position 8), and 3 (position 9).\n\nAnswer: There are 3 Rs in \"strawberry.\"",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "total_tokens": 985,
    "completion_tokens": 970,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null
}
```

</details>


## Shutdown
To shutdown, run
```
sky down deepseek
```
