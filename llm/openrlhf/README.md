# OpenRLHF

## What is OpenRLHF?

Before diving into the implementation, it helps to understand what OpenRLHF actually is. As the name suggests, it is a library to perform Reinforcement Learning from Human Feedback (RLHF). OpenRLHF describes itself as an easy-to-use, high-performance RLHF framework built on Ray, vLLM, ZeRO-3, and HuggingFace Transformers. Despite the growing number of RLHF libraries today, OpenRLHF remains a popular option because of its wide support for training algorithms and its highly optimized codebase.
The framework is still under active development, so for this tutorial I used the latest commit at the time I began the project. That may change as new features land, but the core concepts should remain stable.

The OpenRLHF architecture can appear complex at first glance, but it is built from a few intuitive components:

- Ray: Ray manages distributed execution. OpenRLHF uses it to schedule the various roles in the RLHF pipeline such as actors, critics, reward models, and other processes across multiple GPUs. It also supports a Hybrid Engine mode that allows multiple components to be colocated on the same GPU in order to improve utilization.
- vLLM and AutoTP: A significant portion of RLHF training time is spent generating new trajectories from the policy model. vLLM accelerates this step with optimized inference kernels and distributed features such as tensor parallelism, which make large-model inference fast and scalable.
- ZeRO: Training large language models requires substantial memory, and ZeRO addresses this by sharding parameters and optimizer states across GPUs. It also supports offloading to CPU memory. Through its different stages, it provides fine-grained control over resource usage and efficiency.

## Setting up a SkyPilot Environment

You can set up an environment using the following commands:

```bash
uv venv --seed --python 3.10
source .venv/bin/activate
uv pip install "skypilot[gcp]"
```

The examples below use GCP; however, you're free to use any infrastructure you prefer.
You can configure and install the appropriate libraries by following the instructions in the official [SkyPilot documentation](https://docs.skypilot.co/en/latest/getting-started/installation.html).

## Running OpenRLHF Workloads

The examples are derived from the [DPO](https://openrlhf.readthedocs.io/en/latest/non_rl.html#direct-preference-optimization-dpo), [RM Training](https://openrlhf.readthedocs.io/en/latest/rl.html#reward-model-training), and [SFT](https://openrlhf.readthedocs.io/en/latest/rl.html#supervised-fine-tuning) tutorials present in the OpenRLHF documentation.

They set up the environment within a Docker container, following the recommendations in the Quick Start guide (https://openrlhf.readthedocs.io/en/latest/quick_start.html#installation). The model sizes, batch sizes, and related parameters are scaled down to run efficiently on an 8×A100-40GB cluster and to keep execution time manageable.

All the examples use Spot Instances by default which can easily be changed by setting use_spot to False

Supervised Finetuning (SFT) example -
```
WANDB_TOKEN=xxx HF_TOKEN=xxx sky launch -c open-rlhf-sft openrlhf_sft.yaml --secret WANDB_TOKEN --secret HF_TOKEN
```

Direct Preference Optimization (DPO) example - 
```
WANDB_TOKEN=xxx sky launch -c open-rlhf-dpo openrlhf_dpo.yaml --secret WANDB_TOKEN
```

Reward Model Training example - 
```
WANDB_TOKEN=xxx sky launch -c open-rlhf-rm open-rlhf-rm-training.yaml --secret WANDB_TOKEN
```


