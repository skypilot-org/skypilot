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
    Run LLMs and AI on Any Cloud
</h3>

----
:fire: *News* :fire:
- [Nov, 2023] Example: Using [Axolotl](https://github.com/OpenAccess-AI-Collective/axolotl) to finetune Mistral 7B on the cloud (on-demand and spot): [**example**](./llm/axolotl/)
- [Sep, 2023] [**Mistral 7B**](https://mistral.ai/news/announcing-mistral-7b/), a high-quality open LLM, was released! Deploy via SkyPilot on any cloud: [**Mistral docs**](https://docs.mistral.ai/cloud-deployment/skypilot/)
- [Sep, 2023] Case study: [**Covariant**](https://covariant.ai/) transformed AI development on the cloud using SkyPilot, delivering models 4x faster cost-effectively: [**read the case study**](https://blog.skypilot.co/covariant/)
- [Aug, 2023] Cookbook: Finetuning Llama 2 in your own cloud environment, privately: [**example**](./llm/vicuna-llama-2/), [**blog post**](https://blog.skypilot.co/finetuning-llama2-operational-guide/)
- [July, 2023] Self-Hosted **Llama-2 Chatbot** on Any Cloud: [**example**](./llm/llama-2/)
- [June, 2023] Serving LLM 24x Faster On the Cloud [**with vLLM**](https://vllm.ai/) and SkyPilot: [**example**](./llm/vllm/), [**blog post**](https://blog.skypilot.co/serving-llm-24x-faster-on-the-cloud-with-vllm-and-skypilot/)
- [April, 2023] [SkyPilot YAMLs](./llm/vicuna/) for finetuning & serving the [Vicuna LLM](https://lmsys.org/blog/2023-03-30-vicuna/) with a single command!
----

SkyPilot is a framework for running LLMs, AI, and batch jobs on any cloud, offering maximum cost savings, highest GPU availability, and managed execution.

SkyPilot **abstracts away cloud infra burdens**:
- Launch jobs & clusters on any cloud
- Easy scale-out: queue and run many jobs, automatically managed
- Easy access to object stores (S3, GCS, R2)

SkyPilot **maximizes GPU availability for your jobs**:
* Provision in all zones/regions/clouds you have access to ([the _Sky_](https://arxiv.org/abs/2205.07147)), with automatic failover

SkyPilot **cuts your cloud costs**:
* [Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html): 3-6x cost savings using spot VMs, with auto-recovery from preemptions
* Optimizer: 2x cost savings by auto-picking the cheapest VM/zone/region/cloud
* [Autostop](https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html): hands-free cleanup of idle clusters

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Install with pip:
```bash
pip install "skypilot[aws,gcp,azure,ibm,oci,scp,lambda,kubernetes]"  # choose your clouds
```
To get the latest features/updates, install [from source](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) or the nightly build:
```bash
pip install -U "skypilot-nightly[aws,gcp,azure,ibm,oci,scp,lambda,kubernetes]"  # choose your clouds
```

Current supported providers (AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI, Cloudflare, any Kubernetes cluster):
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=80%>
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
  accelerators: V100:1  # 1x NVIDIA V100 GPU

num_nodes: 1  # Number of VMs to launch

# Working directory (optional) containing the project codebase.
# Its contents are synced to ~/sky_workdir/ on the cluster.
workdir: ~/torch_examples

# Commands to be run before executing the job.
# Typical use: pip install -r requirements.txt, git clone, etc.
setup: |
  pip install torch torchvision

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

Launch with `sky launch` (note: [access to GPU instances](https://skypilot.readthedocs.io/en/latest/reference/quota.html) is needed for this example):
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
To learn more, see our [Documentation](https://skypilot.readthedocs.io/en/latest/) and [Tutorials](https://github.com/skypilot-org/skypilot-tutorial).

Runnable examples:
- LLMs on SkyPilot
  - [Mistral 7B](https://docs.mistral.ai/cloud-deployment/skypilot/) (from official Mistral team)
  - [vLLM: Serving LLM 24x Faster On the Cloud](./llm/vllm/) (from official vLLM team)
  - [Vicuna chatbots: Training & Serving](./llm/vicuna/) (from official Vicuna team)
  - [Train your own Vicuna on Llama-2](./llm/vicuna-llama-2/)
  - [Self-Hosted Llama-2 Chatbot](./llm/llama-2/)
  - [QLoRA](https://github.com/artidoro/qlora/pull/132)
  - [LLaMA-LoRA-Tuner](https://github.com/zetavg/LLaMA-LoRA-Tuner#run-on-a-cloud-service-via-skypilot)
  - [Tabby: Self-hosted AI coding assistant](https://github.com/TabbyML/tabby/blob/bed723fcedb44a6b867ce22a7b1f03d2f3531c1e/experimental/eval/skypilot.yaml)
  - [LocalGPT](./llm/localgpt)
  - [Falcon](./llm/falcon)
  - Add yours here & see more in [`llm/`](./llm)!
- Framework examples: [PyTorch DDP](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_torch.yaml), [DeepSpeed](./examples/deepspeed-multinode/sky.yaml), [JAX/Flax on TPU](https://github.com/skypilot-org/skypilot/blob/master/examples/tpu/tpuvm_mnist.yaml), [Stable Diffusion](https://github.com/skypilot-org/skypilot/tree/master/examples/stable_diffusion), [Detectron2](https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml), [Distributed](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_tf_app.py) [TensorFlow](https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_app_storage.yaml), [NeMo](https://github.com/skypilot-org/skypilot/blob/master/examples/nemo/nemo.yaml), [programmatic grid search](https://github.com/skypilot-org/skypilot/blob/master/examples/huggingface_glue_imdb_grid_search_app.py), [Docker](https://github.com/skypilot-org/skypilot/blob/master/examples/docker/echo_app.yaml), and [many more (`examples/`)](./examples).

Follow updates:
- [Twitter](https://twitter.com/skypilot_org)
- [Slack](http://slack.skypilot.co)
- [SkyPilot Blog](https://blog.skypilot.co/) ([Introductory blog post](https://blog.skypilot.co/introducing-skypilot/))

Read the research:
- [SkyPilot paper](https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf) and [talk](https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng) (NSDI 2023)
- [Sky Computing whitepaper](https://arxiv.org/abs/2205.07147)
- [Sky Computing vision paper](https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf) (HotOS 2021)

## Support and Questions
We are excited to hear your feedback!
* For issues and feature requests, please [open a GitHub issue](https://github.com/skypilot-org/skypilot/issues/new).
* For questions, please use [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).

For general discussions, join us on the [SkyPilot Slack](http://slack.skypilot.co).

## Contributing
We welcome and value all contributions to the project! Please refer to [CONTRIBUTING](CONTRIBUTING.md) for how to get involved.
