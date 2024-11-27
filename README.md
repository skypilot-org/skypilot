<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-dark-1k.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-light-1k.png" width=55%>
  </picture>
</p>

<p align="center">
  <a href="https://skypilot.readthedocs.io/en/latest/">
    <img alt="Documentation" src="https://readthedocs.org/projects/skypilot/badge/?version=latest">
  </a>

  <a href="https://github.com/skypilot-org/skypilot/releases">
    <img alt="GitHub Release" src="https://img.shields.io/github/release/skypilot-org/skypilot.svg">
  </a>

  <a href="http://slack.skypilot.co">
    <img alt="Join Slack" src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack">
  </a>

</p>

<h3 align="center">
    Run AI on Any Infra — Unified, Faster, Cheaper
</h3>

----
:fire: *News* :fire:
- [Oct 2024] :tada: **SkyPilot crossed 1M+ downloads** :tada:: Thank you to our community! [**Twitter/X**](https://x.com/skypilot_org/status/1844770841718067638)
- [Sep 2024] Point, Launch and Serve **Llama 3.2** on Kubernetes or Any Cloud: [**example**](./llm/llama-3_2/)
- [Sep 2024] Run and deploy [**Pixtral**](./llm/pixtral), the first open-source multimodal model from Mistral AI.
- [Jun 2024] Reproduce **GPT** with [llm.c](https://github.com/karpathy/llm.c/discussions/481) on any cloud: [**guide**](./llm/gpt-2/)
- [Apr 2024] Serve [**Qwen-110B**](https://qwenlm.github.io/blog/qwen1.5-110b/) on your infra: [**example**](./llm/qwen/)
- [Apr 2024] Using [**Ollama**](https://github.com/ollama/ollama) to deploy quantized LLMs on CPUs and GPUs: [**example**](./llm/ollama/)
- [Feb 2024] Deploying and scaling [**Gemma**](https://blog.google/technology/developers/gemma-open-models/) with SkyServe: [**example**](./llm/gemma/)
- [Feb 2024] Serving [**Code Llama 70B**](https://ai.meta.com/blog/code-llama-large-language-model-coding/) with vLLM and SkyServe: [**example**](./llm/codellama/)
- [Dec 2023] [**Mixtral 8x7B**](https://mistral.ai/news/mixtral-of-experts/), a high quality sparse mixture-of-experts model, was released by Mistral AI! Deploy via SkyPilot on any cloud: [**example**](./llm/mixtral/)
- [Nov 2023] Using [**Axolotl**](https://github.com/OpenAccess-AI-Collective/axolotl) to finetune Mistral 7B on the cloud (on-demand and spot): [**example**](./llm/axolotl/)

**LLM Finetuning Cookbooks**: Finetuning Llama 2 / Llama 3.1 in your own cloud environment, privately: Llama 2 [**example**](./llm/vicuna-llama-2/) and [**blog**](https://blog.skypilot.co/finetuning-llama2-operational-guide/); Llama 3.1 [**example**](./llm/llama-3_1-finetuning/) and [**blog**](https://blog.skypilot.co/finetune-llama-3_1-on-your-infra/)

<details>
  <summary>Archived</summary>

- [Jul 2024] [**Finetune**](./llm/llama-3_1-finetuning/) and [**serve**](./llm/llama-3_1/) **Llama 3.1** on your infra
- [Apr 2024] Serve and finetune [**Llama 3**](https://skypilot.readthedocs.io/en/latest/gallery/llms/llama-3.html) on any cloud or Kubernetes: [**example**](./llm/llama-3/)
- [Mar 2024] Serve and deploy [**Databricks DBRX**](https://www.databricks.com/blog/introducing-dbrx-new-state-art-open-llm) on your infra: [**example**](./llm/dbrx/)
- [Feb 2024] Speed up your LLM deployments with [**SGLang**](https://github.com/sgl-project/sglang) for 5x throughput on SkyServe: [**example**](./llm/sglang/)
- [Dec 2023] Using [**LoRAX**](https://github.com/predibase/lorax) to serve 1000s of finetuned LLMs on a single instance in the cloud: [**example**](./llm/lorax/)
- [Sep 2023] [**Mistral 7B**](https://mistral.ai/news/announcing-mistral-7b/), a high-quality open LLM, was released! Deploy via SkyPilot on any cloud: [**Mistral docs**](https://docs.mistral.ai/self-deployment/skypilot)
- [Sep 2023] Case study: [**Covariant**](https://covariant.ai/) transformed AI development on the cloud using SkyPilot, delivering models 4x faster cost-effectively: [**read the case study**](https://blog.skypilot.co/covariant/)
- [Jul 2023] Self-Hosted **Llama-2 Chatbot** on Any Cloud: [**example**](./llm/llama-2/)
- [Jun 2023] Serving LLM 24x Faster On the Cloud [**with vLLM**](https://vllm.ai/) and SkyPilot: [**example**](./llm/vllm/), [**blog post**](https://blog.skypilot.co/serving-llm-24x-faster-on-the-cloud-with-vllm-and-skypilot/)
- [Apr 2023] [SkyPilot YAMLs](./llm/vicuna/) for finetuning & serving the [Vicuna LLM](https://lmsys.org/blog/2023-03-30-vicuna/) with a single command!

</details>

----

SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

SkyPilot **abstracts away infra burdens**:
- Launch [dev clusters](https://skypilot.readthedocs.io/en/latest/examples/interactive-development.html), [jobs](https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html), and [serving](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html) on any infra
- Easy job management: queue, run, and auto-recover many jobs

SkyPilot **supports multiple clusters, clouds, and hardware** ([the Sky](https://arxiv.org/abs/2205.07147)):
- Bring your reserved GPUs, Kubernetes clusters, or 12+ clouds
- [Flexible provisioning](https://skypilot.readthedocs.io/en/latest/examples/auto-failover.html) of GPUs, TPUs, CPUs, with auto-retry

SkyPilot **cuts your cloud costs & maximizes GPU availability**:
* [Autostop](https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html): automatic cleanup of idle resources
* [Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html): 3-6x cost savings using spot instances, with preemption auto-recovery
* [Optimizer](https://skypilot.readthedocs.io/en/latest/examples/auto-failover.html): 2x cost savings by auto-picking the cheapest & most available infra

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Install with pip:
```bash
# Choose your clouds:
pip install -U "skypilot[kubernetes,aws,gcp,azure,oci,lambda,runpod,fluidstack,paperspace,cudo,ibm,scp]"
```
To get the latest features and fixes, use the nightly build or [install from source](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html):
```bash
# Choose your clouds:
pip install "skypilot-nightly[kubernetes,aws,gcp,azure,oci,lambda,runpod,fluidstack,paperspace,cudo,ibm,scp]"
```

[Current supported infra](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) (Kubernetes; AWS, GCP, Azure, OCI, Lambda Cloud, Fluidstack, RunPod, Cudo, Paperspace, Cloudflare, Samsung, IBM, VMware vSphere):
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
  </picture>
</p>


## Getting Started
You can find our documentation [here](https://skypilot.readthedocs.io/en/latest/).
- [Installation](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)
- [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html)
- [CLI reference](https://skypilot.readthedocs.io/en/latest/reference/cli.html)

## SkyPilot in 1 Minute

A SkyPilot task specifies: resource requirements, data to be synced, setup commands, and the task commands.

Once written in this [**unified interface**](https://skypilot.readthedocs.io/en/latest/reference/yaml-spec.html) (YAML or Python API), the task can be launched on any available cloud.  This avoids vendor lock-in, and allows easily moving jobs to a different provider.

Paste the following into a file `my_task.yaml`:

```yaml
resources:
  accelerators: A100:8  # 8x NVIDIA A100 GPU

num_nodes: 1  # Number of VMs to launch

# Working directory (optional) containing the project codebase.
# Its contents are synced to ~/sky_workdir/ on the cluster.
workdir: ~/torch_examples

# Commands to be run before executing the job.
# Typical use: pip install -r requirements.txt, git clone, etc.
setup: |
  pip install "torch<2.2" torchvision --index-url https://download.pytorch.org/whl/cu121

# Commands to run as a job.
# Typical use: launch the main program.
run: |
  cd mnist
  python main.py --epochs 1
```

Prepare the workdir by cloning:
```bash
git clone https://github.com/pytorch/examples.git ~/torch_examples
```

Launch with `sky launch` (note: [access to GPU instances](https://skypilot.readthedocs.io/en/latest/cloud-setup/quota.html) is needed for this example):
```bash
sky launch my_task.yaml
```

SkyPilot then performs the heavy-lifting for you, including:
1. Find the lowest priced VM instance type across different clouds
2. Provision the VM, with auto-failover if the cloud returned capacity errors
3. Sync the local `workdir` to the VM
4. Run the task's `setup` commands to prepare the VM for running the task
5. Run the task's `run` commands

<p align="center">
  <img src="https://i.imgur.com/TgamzZ2.gif" alt="SkyPilot Demo"/>
</p>


Refer to [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html) to get started with SkyPilot.

## More Information
To learn more, see our [documentation](https://skypilot.readthedocs.io/en/latest/), [blog](https://blog.skypilot.co/), and [community integrations](https://blog.skypilot.co/community/).

<!-- Keep this section in sync with index.rst in SkyPilot Docs -->
Runnable examples:
- LLMs on SkyPilot
  - [Llama 3.2: lightweight and vision models](./llm/llama-3_2/)
  - [Pixtral](./llm/pixtral/)
  - [Llama 3.1 finetuning](./llm/llama-3_1-finetuning/) and [serving](./llm/llama-3_1/)
  - [GPT-2 via `llm.c`](./llm/gpt-2/)
  - [Llama 3](./llm/llama-3/)
  - [Qwen](./llm/qwen/)
  - [Databricks DBRX](./llm/dbrx/)
  - [Gemma](./llm/gemma/)
  - [Mixtral 8x7B](./llm/mixtral/); [Mistral 7B](https://docs.mistral.ai/self-deployment/skypilot/) (from official Mistral team)
  - [Code Llama](./llm/codellama/)
  - [vLLM: Serving LLM 24x Faster On the Cloud](./llm/vllm/) (from official vLLM team)
  - [SGLang: Fast and Expressive LLM Serving On the Cloud](./llm/sglang/) (from official SGLang team)
  - [Vicuna chatbots: Training & Serving](./llm/vicuna/) (from official Vicuna team)
  - [Train your own Vicuna on Llama-2](./llm/vicuna-llama-2/)
  - [Self-Hosted Llama-2 Chatbot](./llm/llama-2/)
  - [Ollama: Quantized LLMs on CPUs](./llm/ollama/)
  - [LoRAX](./llm/lorax/)
  - [QLoRA](https://github.com/artidoro/qlora/pull/132)
  - [LLaMA-LoRA-Tuner](https://github.com/zetavg/LLaMA-LoRA-Tuner#run-on-a-cloud-service-via-skypilot)
  - [Tabby: Self-hosted AI coding assistant](https://github.com/TabbyML/tabby/blob/bed723fcedb44a6b867ce22a7b1f03d2f3531c1e/experimental/eval/skypilot.yaml)
  - [LocalGPT](./llm/localgpt)
  - [Falcon](./llm/falcon)
  - Add yours here & see more in [`llm/`](./llm)!
- Framework examples: [PyTorch DDP](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_torch.yaml), [DeepSpeed](./examples/deepspeed-multinode/sky.yaml), [JAX/Flax on TPU](https://github.com/skypilot-org/skypilot/blob/master/examples/tpu/tpuvm_mnist.yaml), [Stable Diffusion](https://github.com/skypilot-org/skypilot/tree/master/examples/stable_diffusion), [Detectron2](https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml), [Distributed](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_tf_app.py) [TensorFlow](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_app_storage.yaml), [Ray Train](examples/distributed_ray_train/ray_train.yaml), [NeMo](https://github.com/skypilot-org/skypilot/blob/master/examples/nemo/), [programmatic grid search](https://github.com/skypilot-org/skypilot/blob/master/examples/huggingface_glue_imdb_grid_search_app.py), [Docker](https://github.com/skypilot-org/skypilot/blob/master/examples/docker/echo_app.yaml), [Cog](https://github.com/skypilot-org/skypilot/blob/master/examples/cog/), [Unsloth](https://github.com/skypilot-org/skypilot/blob/master/examples/unsloth/unsloth.yaml), [Ollama](https://github.com/skypilot-org/skypilot/blob/master/llm/ollama), [llm.c](https://github.com/skypilot-org/skypilot/tree/master/llm/gpt-2), [Airflow](./examples/airflow/training_workflow) and [many more (`examples/`)](./examples).

Case Studies and Integrations: [Community Spotlights](https://blog.skypilot.co/community/)

Follow updates:
- [Twitter](https://twitter.com/skypilot_org)
- [Slack](http://slack.skypilot.co)
- [SkyPilot Blog](https://blog.skypilot.co/) ([Introductory blog post](https://blog.skypilot.co/introducing-skypilot/))

Read the research:
- [SkyPilot paper](https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf) and [talk](https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng) (NSDI 2023)
- [Sky Computing whitepaper](https://arxiv.org/abs/2205.07147)
- [Sky Computing vision paper](https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf) (HotOS 2021)
- [Policy for Managed Spot Jobs](https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao)  (NSDI 2024)

## Support and Questions
We are excited to hear your feedback!
* For issues and feature requests, please [open a GitHub issue](https://github.com/skypilot-org/skypilot/issues/new).
* For questions, please use [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).

For general discussions, join us on the [SkyPilot Slack](http://slack.skypilot.co).

## Contributing
We welcome all contributions to the project! See [CONTRIBUTING](CONTRIBUTING.md) for how to get involved.
