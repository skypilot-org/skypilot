<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-dark-1k.png">
    <img alt="SkyPilot" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/skypilot-wide-light-1k.png" width=55%>
  </picture>
</p>

[![Join Slack](https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack)](https://join.slack.com/t/skypilot-org/shared_invite/zt-1i4pa7lyc-g6Lo4_rqqCFWOSXdvwTs3Q)
![pytest](https://github.com/skypilot-org/skypilot/actions/workflows/pytest.yml/badge.svg)
[![Documentation Status](https://readthedocs.org/projects/skypilot/badge/?version=latest)](https://skypilot.readthedocs.io/en/latest/?badge=latest)

SkyPilot is a framework for easily running machine learning workloads[^1] on any cloud. 

Use the clouds **easily** and **cost effectively**, without needing cloud infra expertise.

_Ease of use_
* **Run existing projects on the cloud** with zero code changes
* Use a **unified interface** to run on any cloud, without vendor lock-in (currently AWS, Azure, GCP)
* **Queue jobs** on one or multiple clusters
* **Automatic failover** to find scarce resources (GPUs) across regions and clouds
* **Use datasets on the cloud** like you would on a local file system 

_Cost saving_
* Run jobs on **spot instances** with **automatic recovery** from preemptions
* Hands-free cluster management: **automatically stopping idle clusters**
* One-click use of **TPUs**, for high-performance, cost-effective training
* Automatically benchmark and find the cheapest hardware for your job

## Getting Started
You can find our documentation [here](https://skypilot.readthedocs.io/en/latest/).
- [Installation](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)
- [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html)
- [CLI reference](https://skypilot.readthedocs.io/en/latest/reference/cli.html)

## Example SkyPilot Task

A SkyPilot task specifies: resource requirements, data to be synced, setup commands, and the task commands. 

Once written in this [**unified interface**](https://skypilot.readthedocs.io/en/latest/reference/yaml-spec.html) (YAML or Python API), the task can be launched on any available cloud. 

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


See [**`examples`**](./examples) for more YAMLs that run popular ML frameworks on the cloud with one command (PyTorch/Distributed PyTorch, TensorFlow/Distributed TensorFlow, HuggingFace, JAX, Flax, Docker).  

Besides YAML, SkyPilot offers a corresponding [**Python API**](https://github.com/skypilot-org/skypilot/blob/master/sky/core.py) for programmatic use.

Refer to [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html) for more on how to get started with SkyPilot.


## Issues, feature requests and questions
We are excited to hear your feedback! SkyPilot has two channels for engaging with the community - [GitHub Issues](https://github.com/skypilot-org/skypilot/issues) and [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).
* For bug reports and issues, please [open an issue](https://github.com/skypilot-org/skypilot/issues/new).
* For feature requests or general questions, please join us on [GitHub Discussions](https://github.com/skypilot-org/skypilot/discussions).

## Contributing
We welcome and value all contributions to the project! Please refer to the [contribution guide](CONTRIBUTING.md) for more on how to get involved.

<!-- Footnote -->
[^1]: While SkyPilot is currently targeted at machine learning workloads, it supports and has been used for other general workloads. We're excited to hear about your use case and how we can better support your requirements - please join us in [this discussion](https://github.com/skypilot-org/skypilot/discussions/1016)!
