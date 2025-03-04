<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-dark-1k.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-light-1k.png" width=55%>
  </picture>
</p>

<p align="center">
  <a href="https://docs.skypilot.co/">
    <img alt="Documentation" src="https://img.shields.io/badge/docs-gray?logo=readthedocs&logoColor=f5f5f5">
  </a>

  <a href="https://github.com/skypilot-org/skypilot/releases">
    <img alt="GitHub Release" src="https://img.shields.io/github/release/skypilot-org/skypilot.svg">
  </a>

  <a href="http://slack.skypilot.co">
    <img alt="Join Slack" src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack">
  </a>

  <a href="https://github.com/skypilot-org/skypilot/releases">
    <img alt="Downloads" src="https://img.shields.io/pypi/dm/skypilot">
  </a>

</p>

<h3 align="center">
    Run AI on Any Infra — Unified, Faster, Cheaper
</h3>

----
:fire: *News* :fire:
- [Feb 2025] Prepare and serve **Retrieval Augmented Generation (RAG) with DeepSeek-R1**: [**blog post**](https://blog.skypilot.co/deepseek-rag), [**example**](./llm/rag/)
- [Feb 2025] Run and serve **DeepSeek-R1 671B** using SkyPilot and SGLang with high throughput: [**example**](./llm/deepseek-r1/)
- [Feb 2025] Prepare and serve large-scale image search with **vector databases**: [**blog post**](https://blog.skypilot.co/large-scale-vector-database/), [**example**](./examples/vector_database/)
- [Jan 2025] Launch and serve distilled models from **[DeepSeek-R1](https://github.com/deepseek-ai/DeepSeek-R1)** and **[Janus](https://github.com/deepseek-ai/DeepSeek-Janus)** on Kubernetes or any cloud: [**R1 example**](./llm/deepseek-r1-distilled/) and [**Janus example**](./llm/deepseek-janus/)
- [Oct 2024] :tada: **SkyPilot crossed 1M+ downloads** :tada:: Thank you to our community! [**Twitter/X**](https://x.com/skypilot_org/status/1844770841718067638)
- [Sep 2024] Point, launch and serve **Llama 3.2** on Kubernetes or any cloud: [**example**](./llm/llama-3_2/)
- [Sep 2024] Run and deploy [**Pixtral**](./llm/pixtral), the first open-source multimodal model from Mistral AI.
- [Jun 2024] Reproduce **GPT** with [llm.c](https://github.com/karpathy/llm.c/discussions/481) on any cloud: [**guide**](./llm/gpt-2/)
- [Apr 2024] Serve [**Qwen-110B**](https://qwenlm.github.io/blog/qwen1.5-110b/) on your infra: [**example**](./llm/qwen/)
- [Apr 2024] Host [**Ollama**](https://github.com/ollama/ollama) on the cloud to deploy LLMs on CPUs and GPUs: [**example**](./llm/ollama/)


**LLM Finetuning Cookbooks**: Finetuning Llama 2 / Llama 3.1 in your own cloud environment, privately: Llama 2 [**example**](./llm/vicuna-llama-2/) and [**blog**](https://blog.skypilot.co/finetuning-llama2-operational-guide/); Llama 3.1 [**example**](./llm/llama-3_1-finetuning/) and [**blog**](https://blog.skypilot.co/finetune-llama-3_1-on-your-infra/)

----

SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

SkyPilot **abstracts away infra burdens**:
- Launch [clusters](https://docs.skypilot.co/en/latest/examples/interactive-development.html), [jobs](https://docs.skypilot.co/en/latest/examples/managed-jobs.html), and [serving](https://docs.skypilot.co/en/latest/serving/sky-serve.html) on any infra
- Easy job management: queue, run, and auto-recover many jobs

SkyPilot **supports multiple clusters, clouds, and hardware** ([the Sky](https://arxiv.org/abs/2205.07147)):
- Bring your reserved GPUs, Kubernetes clusters, or 12+ clouds
- [Flexible provisioning](https://docs.skypilot.co/en/latest/examples/auto-failover.html) of GPUs, TPUs, CPUs, with auto-retry

SkyPilot **cuts your cloud costs & maximizes GPU availability**:
* [Autostop](https://docs.skypilot.co/en/latest/reference/auto-stop.html): automatic cleanup of idle resources
* [Managed Spot](https://docs.skypilot.co/en/latest/examples/managed-jobs.html): 3-6x cost savings using spot instances, with preemption auto-recovery
* [Optimizer](https://docs.skypilot.co/en/latest/examples/auto-failover.html): 2x cost savings by auto-picking the cheapest & most available infra

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Install with pip:
```bash
# Choose your clouds:
pip install -U "skypilot[kubernetes,aws,gcp,azure,oci,lambda,runpod,fluidstack,paperspace,cudo,ibm,scp,nebius]"
```
To get the latest features and fixes, use the nightly build or [install from source](https://docs.skypilot.co/en/latest/getting-started/installation.html):
```bash
# Choose your clouds:
pip install "skypilot-nightly[kubernetes,aws,gcp,azure,oci,lambda,runpod,fluidstack,paperspace,cudo,ibm,scp,nebius]"
```


[Current supported infra](https://docs.skypilot.co/en/latest/getting-started/installation.html) (Kubernetes; AWS, GCP, Azure, OCI, Lambda Cloud, Fluidstack, RunPod, Cudo, Digital Ocean, Paperspace, Cloudflare, Samsung, IBM, Vast.ai, VMware vSphere, Nebius):
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
  </picture>
</p>


## Getting started
You can find our documentation [here](https://docs.skypilot.co/).
- [Installation](https://docs.skypilot.co/en/latest/getting-started/installation.html)
- [Quickstart](https://docs.skypilot.co/en/latest/getting-started/quickstart.html)
- [CLI reference](https://docs.skypilot.co/en/latest/reference/cli.html)

## SkyPilot in 1 minute

A SkyPilot task specifies: resource requirements, data to be synced, setup commands, and the task commands.

Once written in this [**unified interface**](https://docs.skypilot.co/en/latest/reference/yaml-spec.html) (YAML or Python API), the task can be launched on any available cloud.  This avoids vendor lock-in, and allows easily moving jobs to a different provider.

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

Launch with `sky launch` (note: [access to GPU instances](https://docs.skypilot.co/en/latest/cloud-setup/quota.html) is needed for this example):
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


See [Quickstart](https://docs.skypilot.co/en/latest/getting-started/quickstart.html) to get started with SkyPilot.

## Runnable examples

See [**SkyPilot examples**](https://docs.skypilot.co/en/docs-examples/examples/index.html) that cover: development, training, serving, LLM models, AI apps, and common frameworks.

Latest featured examples:

| Task | Examples |
|----------|----------|
| Training | [PyTorch](https://docs.skypilot.co/en/latest/getting-started/tutorial.html), [DeepSpeed](https://docs.skypilot.co/en/latest/examples/training/deepspeed.html), [Finetune Llama 3](https://docs.skypilot.co/en/latest/examples/training/llama-3_1-finetuning.html), [NeMo](https://docs.skypilot.co/en/latest/examples/training/nemo.html), [Ray](https://docs.skypilot.co/en/latest/examples/training/ray.html), [Unsloth](https://docs.skypilot.co/en/latest/examples/training/unsloth.html), [Jax/TPU](https://docs.skypilot.co/en/latest/examples/training/tpu.html) |
| Serving | [vLLM](https://docs.skypilot.co/en/latest/examples/serving/vllm.html), [SGLang](https://docs.skypilot.co/en/latest/examples/serving/sglang.html), [Ollama](https://docs.skypilot.co/en/latest/examples/serving/ollama.html) |
| Models | [DeepSeek-R1](https://docs.skypilot.co/en/latest/examples/models/deepseek-r1.html), [Llama 3](https://docs.skypilot.co/en/latest/examples/models/llama-3.html), [CodeLlama](https://docs.skypilot.co/en/latest/examples/models/codellama.html), [Qwen](https://docs.skypilot.co/en/latest/examples/models/qwen.html), [Mixtral](https://docs.skypilot.co/en/latest/examples/models/mixtral.html) |
| AI apps | [RAG](https://docs.skypilot.co/en/latest/examples/applications/rag.html), [vector databases](https://docs.skypilot.co/en/latest/examples/applications/vector_database.html) (ChromaDB, CLIP) |
| Common frameworks | [Airflow](https://docs.skypilot.co/en/latest/examples/frameworks/airflow.html), [Jupyter](https://docs.skypilot.co/en/latest/examples/frameworks/jupyter.html) |

Source files and more examples can be found in [`llm/`](https://github.com/skypilot-org/skypilot/tree/master/llm) and [`examples/`](https://github.com/skypilot-org/skypilot/tree/master/examples).

## More information
To learn more, see [SkyPilot Overview](https://docs.skypilot.co/en/latest/overview.html), [SkyPilot docs](https://docs.skypilot.co/en/latest/), and [SkyPilot blog](https://blog.skypilot.co/).

Case studies and integrations: [Community Spotlights](https://blog.skypilot.co/community/)

Follow updates:
- [Slack](http://slack.skypilot.co)
- [X / Twitter](https://twitter.com/skypilot_org)
- [LinkedIn](https://www.linkedin.com/company/skypilot-oss/)
- [SkyPilot Blog](https://blog.skypilot.co/) ([Introductory blog post](https://blog.skypilot.co/introducing-skypilot/))

Read the research:
- [SkyPilot paper](https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf) and [talk](https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng) (NSDI 2023)
- [Sky Computing whitepaper](https://arxiv.org/abs/2205.07147)
- [Sky Computing vision paper](https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf) (HotOS 2021)
- [SkyServe: AI serving across regions and clouds](https://arxiv.org/pdf/2411.01438) (EuroSys 2025)
- [Managed jobs spot instance policy](https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao)  (NSDI 2024)

SkyPilot was initially started at the [Sky Computing Lab](https://sky.cs.berkeley.edu) at UC Berkeley and has since gained many industry contributors. To read about the project's origin and vision, see [Concept: Sky Computing](https://docs.skypilot.co/en/latest/sky-computing.html).

## Questions and feedback
We are excited to hear your feedback:
* For issues and feature requests, please [open a GitHub issue](https://github.com/skypilot-org/skypilot/issues/new).
* For questions, please use [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).

For general discussions, join us on the [SkyPilot Slack](http://slack.skypilot.co).

## Contributing
We welcome all contributions to the project! See [CONTRIBUTING](CONTRIBUTING.md) for how to get involved.
