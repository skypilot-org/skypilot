

📰 **Update (26 April 2024) -** SkyPilot now also supports the [**MiniCPM-2B**](https://openbmb.vercel.app/?category=Chinese+Blog/) model! Use [serve-2b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/minicpm/serve-2b.yaml) to serve the 110B model.

📰 **Update (6 Jun 2024) -** SkyPilot now also supports the [**MiniCPM-1B**](https://openbmb.vercel.app/?category=Chinese+Blog/) model! 

<p align="center">
    <img src="https://i.imgur.com/d7tEhAl.gif" alt="qwen" width="600"/>
</p>

## References
* [MiniCPM blog](https://openbmb.vercel.app/?category=Chinese+Blog/)

## Why use SkyPilot to deploy over commercial hosted solutions?

* Get the best GPU availability by utilizing multiple resources pools across multiple regions and clouds.
* Pay absolute minimum — SkyPilot picks the cheapest resources across regions and clouds. No managed solution markups.
* Scale up to multiple replicas across different locations and accelerators, all served with a single endpoint 
* Everything stays in your cloud account (your VMs & buckets)
* Completely private - no one else sees your chat history


## Running your own Minicpm with SkyPilot

After [installing SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html), run your own minicpm model on vLLM with SkyPilot in 1-click:

1. Start serving MiniCPM on a single instance with any available GPU in the list specified in [serve-2b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/minicpm/serve-2b.yaml) with a vLLM powered OpenAI-compatible endpoint (You can also switch to [serve-1b.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/minicpm/serve-1b.yaml) or [serve-cpmv2_6.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/minicpm/serve-cpmv2_6.yaml) for a multimodal model):

```bash
sky launch -c cpm serve-110b.yaml
```
2. Send a request to the endpoint for completion:
```bash
IP=$(sky status --ip qwen)

curl http://$IP:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "openbmb/MiniCPM-2B-sft-bf16",
      "prompt": "My favorite food is",
      "max_tokens": 512
  }' | jq -r '.choices[0].text'
```

3. Send a request for chat completion:
```bash
curl http://$IP:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "openbmb/MiniCPM-1B-sft-bf16",
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


## **Optional:** Accessing Qwen with Chat GUI

It is also possible to access the Qwen service with a GUI using [vLLM](https://github.com/vllm-project/vllm).

1. Start the chat web UI (change the `--env` flag to the model you are running):
```bash
sky launch -c cpm-gui ./gui.yaml --env MODEL_NAME='openbmb/MiniCPM-2B-sft-bf16' --env ENDPOINT=$(sky serve status --endpoint cpm)
```

2. Then, we can access the GUI at the returned gradio link:
```
| INFO | stdout | Running on public URL: https://6141e84201ce0bb4ed.gradio.live
```

