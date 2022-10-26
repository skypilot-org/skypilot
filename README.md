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
  
  <a href="https://join.slack.com/t/skypilot-org/shared_invite/zt-1i4pa7lyc-g6Lo4_rqqCFWOSXdvwTs3Q"> 
    <img alt="Join Slack" src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack">
  </a>
  
</p>


<h4 align="center">
    Run jobs on any cloud, easily and cost effectively
</h4>

SkyPilot is a framework for easily running machine learning workloads[^1] on any cloud. 

SkyPilot abstracts away cloud infra burden:
- Launch jobs & clusters on any cloud (AWS, Azure, GCP)
- Find availability across zones/regions/clouds
- Job queueing & using cloud object stores

SkyPilot cuts your cloud costs:
* [Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html): **3x cost savings** using spot VMs, with auto-recovery from preemptions
* [Autostop](https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html): hands-free cleanup of idle clusters 
* [Benchmark](https://skypilot.readthedocs.io/en/latest/reference/benchmark/index.html): auto-selects best VM types for your jobs
* Optimizer: auto-picks best prices across zones/regions/clouds

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code change required. 

Install with (choose your clouds)
```
pip install "skypilot[aws,gcp,azure]"
```

## Getting Started
You can find our documentation [here](https://skypilot.readthedocs.io/en/latest/).
- [Installation](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)
- [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html)
- [CLI reference](https://skypilot.readthedocs.io/en/latest/reference/cli.html)

## SkyPilot in 1 minute

A SkyPilot task specifies: resource requirements, data to be synced, setup commands, and the task commands. 

Once written in this [**unified interface**](https://skypilot.readthedocs.io/en/latest/reference/yaml-spec.html) (YAML or Python API), the task can be launched on any available cloud.  This avoids vendor lock-in, and allows easily moving jobs to a different provider.

Example:

```yaml
# my_task.yaml
resources:
  # 1x NVIDIA V100 GPU
  accelerators: V100:1

# Number of VMs to launch in the cluster
num_nodes: 1

# Working directory (optional) containing the project codebase.
# Its contents are synced to ~/sky_workdir/ on the cluster.
workdir: ~/torch_examples

# Commands to be run before executing the job
# Typical use: pip install -r requirements.txt, git clone, etc.
setup: |
  pip install torch torchvision

# Commands to run as a job
# Typical use: make use of resources, such as running training.
run: |
  cd mnist
  python main.py --epochs 1
```

Prepare the workdir by cloning locally:
```bash
git clone https://github.com/pytorch/examples.git ~/torch_examples
```

Launch with `sky launch`:
```bash
sky launch my_task.yaml
```
SkyPilot will perform multiple actions for you:
1. Find the lowest priced VM instance type across different clouds
2. Provision the VM
3. Copy the local contents of `workdir` to the VM
4. Run the task's `setup` commands to prepare the VM for running the task 
5. Run the task's `run` commands

<p align="center">
  <img src="https://i.imgur.com/TgamzZ2.gif" alt="SkyPilot Demo"/>
</p>


Refer to [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html) to get started with SkyPilot.

## Learn more

- [Documentation](https://skypilot.readthedocs.io/en/latest/)
- [Example: HuggingFace](https://skypilot.readthedocs.io/en/latest/getting-started/tutorial.html) 
- [Tutorials](https://github.com/skypilot-org/skypilot-tutorial) 
- [YAML reference](https://skypilot.readthedocs.io/en/latest/reference/yaml-spec.html)
- [**`examples`**](./examples): run popular ML frameworks on the cloud with one command


## Issues, feature requests, and questions
We are excited to hear your feedback! 
* For bug reports and issues, please [open a GitHub issue](https://github.com/skypilot-org/skypilot/issues/new).
* For feature requests or general questions, please join us on [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).

## Contributing
We welcome and value all contributions to the project! Please refer to the [contribution guide](CONTRIBUTING.md) for more on how to get involved.

<!-- Footnote -->
[^1]: While SkyPilot is currently targeted at machine learning workloads, it supports and has been used for other general workloads. We're excited to hear about your use case and how we can better support your requirements - please join us in [this discussion](https://github.com/skypilot-org/skypilot/discussions/1016)!
