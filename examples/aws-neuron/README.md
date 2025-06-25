# AWS Inferentia

SkyPilot supports AWS Inferentia accelerators. The Neuron SDK is a runtime and compiler for running deep learning models on AWS Inferentia chips. Here is an example of how to use the Neuron SDK to launch a Llama 3 8b model on an Inferentia chip:

```bash
$ sky launch -c aws-inf inferentia.yaml --secret HF_TOKEN=hf_xxx
```

To send an example request to the model, you can use the following command:

```bash
$ ENDPOINT=$(sky status aws-inf --endpoint 9000)
$ curl http://$ENDPOINT/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "meta-llama/Meta-Llama-3-8B-Instruct",
      "messages": [
        {
          "role": "system",
          "content": "You are a helpful assistant."
        },
        {
          "role": "user",
          "content": "Who are you?"
        }
      ],
      "stop_token_ids": [128009, 128001]
    }'
{"id":"chat-0631550312c143d88ca6d477d0df6c2c","object":"chat.completion","created":1727751137,"model":"meta-llama/Meta-Llama-3-8B-Instruct","choices":[{"index":0,"message":{"role":"assistant","content":"I'm a helpful assistant! I","tool_calls":[]},"logprobs":null,"finish_reason":"length","stop_reason":null}],"usage":{"prompt_tokens":25,"total_tokens":32,"completion_tokens":7},"prompt_logprobs":null}
```

## Using multiple accelerator choices

You can also specify multiple resources in a task YAML to allow SkyPilot to find the cheapest available resources for you. Specifically, you can specify both Neuron accelerators and Nvidia GPUs in the same YAML file. Here is an example (See [multi-accelerator.yaml](./multi-accelerator.yaml)):

<details>

<summary>Example YAML for multiple accelerators.</summary>

```yaml
resources:
  accelerators: {A100:1, Inferentia:6}
  disk_size: 512
  ports: 9000

envs:
  MODEL_NAME: meta-llama/Meta-Llama-3-8B-Instruct
secrets:
  HF_TOKEN: null # Pass with `--secret HF_TOKEN` in CLI

setup: |
  if command -v nvidia-smi; then
    pip install vllm==0.4.2
    pip install flash-attn==2.5.9.post1
  else
    # Install transformers-neuronx and its dependencies
    sudo apt-get install -y python3.10-venv g++
    python3.10 -m venv aws_neuron_venv_pytorch
    source aws_neuron_venv_pytorch/bin/activate
    pip install ipykernel
    python3.10 -m ipykernel install --user --name aws_neuron_venv_pytorch --display-name "Python (torch-neuronx)"
    pip install jupyter notebook
    pip install environment_kernels
    python -m pip config set global.extra-index-url https://pip.repos.neuron.amazonaws.com
    python -m pip install wget
    python -m pip install awscli
    python -m pip install --upgrade neuronx-cc==2.* --pre torch-neuronx==2.1.* torchvision transformers-neuronx

    # Install latest version of triton.
    # Reference: https://github.com/vllm-project/vllm/issues/6987
    pip install -U --index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/Triton-Nightly/pypi/simple triton-nightly

    # Install vLLM from source. Avoid using dir name 'vllm' due to import conflict.
    # Reference: https://github.com/vllm-project/vllm/issues/1814#issuecomment-1837122930
    git clone https://github.com/vllm-project/vllm.git vllm_repo
    cd vllm_repo
    pip install -U -r requirements-neuron.txt
    VLLM_TARGET_DEVICE="neuron" pip install -e .

    python -c "import huggingface_hub; huggingface_hub.login('${HF_TOKEN}')"

    sudo apt update
    sudo apt install -y numactl
  fi

run: |
  if command -v nvidia-smi; then
    TENSOR_PARALLEL_SIZE=$SKYPILOT_NUM_GPUS_PER_NODE
    PREFIX=""
    DEVICE="cuda"
  else
    source aws_neuron_venv_pytorch/bin/activate
    # Calculate the tensor parallel size. vLLM requires the tensor parallel size
    # to be a factor of the number of attention heads, which is 32 for the model.
    # Here we calculate the largest power of 2 that is less than or equal to the
    # number of GPUs per node.
    TENSOR_PARALLEL_SIZE=1
    while [ $(($TENSOR_PARALLEL_SIZE * 2)) -le $SKYPILOT_NUM_GPUS_PER_NODE ]; do
      TENSOR_PARALLEL_SIZE=$(($TENSOR_PARALLEL_SIZE * 2))
    done
    NEURON_RT_VISIBLE_CORES="0-$(($TENSOR_PARALLEL_SIZE - 1))"
    OMP_NUM_THREADS=$SKYPILOT_NUM_GPUS_PER_NODE
    MASTER_PORT=12355
    LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/home/ubuntu/miniconda3/lib"
    PREFIX="numactl --cpunodebind=0 --membind=0"
    DEVICE="neuron"
  fi
  $PREFIX python3 -m vllm.entrypoints.openai.api_server \
    --device $DEVICE \
    --model $MODEL_NAME \
    --tensor-parallel-size $TENSOR_PARALLEL_SIZE \
    --max-num-seqs 16 \
    --max-model-len 32 \
    --block-size 32 \
    --port 9000
```

</details>
