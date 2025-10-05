# Vicuna: An LLM Chatbot Impressing GPT-4 with 90% ChatGPT Quality

<p align="center">
    <img src="https://i.imgur.com/z3AOYLV.png" alt="Vicuna LLM"/>
</p>

This README contains instructions to run and train Vicuna, an open-source LLM chatbot with quality comparable to ChatGPT. The Vicuna release was trained using SkyPilot on [cloud spot instances](https://docs.skypilot.co/en/latest/examples/spot-jobs.html), with a cost of ~$300.

* [Blog post](https://lmsys.org/blog/2023-03-30-vicuna/)
* [Demo](https://chat.lmsys.org/)
* [Repo](https://github.com/lm-sys/FastChat)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See the Vicuna SkyPilot YAMLs: for [training](https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/train.yaml) and for [serving](https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/serve.yaml).

## Serve the official Vicuna model by yourself with SkyPilot

1. Start serving the Vicuna-7B model on a single A100 GPU:
```bash
sky launch -c vicuna-serve -s serve.yaml
```
2. Check the output of the command. There will be a sharable gradio link (like the last line of the following). Open it in your browser to chat with Vicuna.
```
(task, pid=20933) 2023-04-12 22:08:49 | INFO | gradio_web_server | Namespace(host='0.0.0.0', port=None, controller_url='http://localhost:21001', concurrency_count=10, model_list_mode='once', share=True, moderate=False)
(task, pid=20933) 2023-04-12 22:08:49 | INFO | stdout | Running on local URL:  http://0.0.0.0:7860
(task, pid=20933) 2023-04-12 22:08:51 | INFO | stdout | Running on public URL: https://<random-hash>.gradio.live
```

3. [Optional] Try other GPUs:
```bash
sky launch -c vicuna-serve-v100 -s serve.yaml --gpus V100
```

4. [Optional] Serve the 13B model instead of the default 7B:
```bash
sky launch -c vicuna-serve -s serve.yaml --env MODEL_SIZE=13
```

5. [Optional] Serve the OpenAI API Compatible Endpoint:
```bash
sky launch -c vicuna-openai-api -s serve-openai-api-endpoint.yaml
```


## Training Vicuna with SkyPilot
Currently, training requires GPUs with 80GB memory.  See `sky show-gpus --all` for supported GPUs.

We can start the training of Vicuna model on the dummy data [dummy.json](https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/dummy.json)[^1] **with a single command**. It will automatically find the available cheapest VM on any cloud.

**To train on your own data**, replace the file with your own, or change the line `/data/mydata.json: ./dummy.json` to the path of your own data in the [train.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/train.yaml).

[^1]: The dummy data was originally from the official Vicuna repository, [FastChat](https://github.com/lm-sys/FastChat).

Steps for training on your cloud(s):

1. Replace the bucket name in [train.yaml](https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna/train.yaml) with some unique name, so the SkyPilot can create a bucket for you to store the model weights. See `# Change to your own bucket` in the YAML file.

2. **Training the Vicuna-7B model on 8 A100 GPUs (80GB memory) using spot instances**:
```bash
# Launch it on managed spot to save 3x cost
sky jobs launch -n vicuna train.yaml
```
Note: if you would like to see the training curve on W&B, you can add `--secret WANDB_API_KEY` to the above command, which will propagate your local W&B API key securely to the job.

[Optional] Train a larger 13B model
```
# Train a 13B model instead of the default 7B
sky jobs launch -n vicuna-7b train.yaml --env MODEL_SIZE=13

# Use *unmanaged* spot instances (i.e., preemptions won't get auto-recovered).
# Unmanaged spot provides a better interactive development experience but is vulnerable to spot preemptions.
# We recommend using managed spot as above.
sky launch -c vicuna train.yaml
```
Currently, such `A100-80GB:8` spot instances are only available on AWS and GCP.

[Optional] **To use on-demand `A100-80GB:8` instances**, which are currently available on Lambda Cloud, Azure, and GCP:
```bash
sky launch -c vicuna -s train.yaml --no-use-spot
```



## Q&A

Q: I see some bucket permission errors `sky.exceptions.StorageBucketGetError` when running the above:
```
...
sky.exceptions.StorageBucketGetError: Failed to connect to an existing bucket 'YOUR_OWN_BUCKET_NAME'.
Please check if:
  1. the bucket name is taken and/or
  2. the bucket permissions are not setup correctly. To debug, consider using gsutil ls gs://YOUR_OWN_BUCKET_NAME.
```

A: You need to replace the bucket name with your own globally unique name, and rerun the commands. New private buckets will be automatically created under your cloud account.
