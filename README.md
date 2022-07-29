
![logo](docs/source/images/SkyPilot-logo-wide.png)

![pytest](https://github.com/skypilot-org/skypilot/actions/workflows/pytest.yml/badge.svg)
[![Documentation Status](https://readthedocs.org/projects/skypilot/badge/?version=latest)](https://skypilot.readthedocs.io/en/latest/?badge=latest)

SkyPilot is a framework to run machine learning[^1] workloads seamlessly across different cloud providers through a unified interface. No knowledge of cloud offerings is required or expected – you simply define the workload and its resource requirements, and SkyPilot will automatically execute it on AWS, Google Cloud Platform or Microsoft Azure.

[^1]: SkyPilot is primarily targeted at machine learning workloads, but it can also support many general workloads. We're excited to hear about your use case and would love to hear more about how we can better support your needs - please join us in [this discussion](https://github.com/skypilot-org/skypilot/discussions/1016)!

### Key features
* **Run existing projects on the cloud** with zero code changes
* **No cloud lock-in** – seamlessly run your code across different cloud providers (AWS, Azure or GCP)
* **Minimize costs** by leveraging spot instances and automatically stopping idle clusters
* **Automatic recovery from spot instance failures**
* **Automatic fail-over** to find resources across regions and clouds
* **Store datasets on the cloud** and access them like you would on a local file system 
* **Easily manage job queues** across multiple clusters


## Getting Started
You can find our documentation [here](https://skypilot.readthedocs.io/en/latest/).
- [Installation](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)
- [Quickstart](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html)
- [CLI reference](https://skypilot.readthedocs.io/en/latest/reference/cli.html)

## Example SkyPilot Task

Tasks in SkyPilot are specified as a YAML file containing the resource requirements, data to be synced, setup commands and the task commands. Here is an example.

```yaml
# my-task.yaml
resources:
  # 1x NVIDIA V100 GPU
  accelerators: V100:1

# Number of VMs to launch in the cluster
num_nodes: 1

# Working directory (optional) containing the project codebase.
# Its contents are synced to ~/sky_workdir/ on the cluster.
workdir: .

# Commands to be run before executing the job
# Typical use: pip install -r requirements.txt, git clone, etc.
setup: |
  echo "Running setup."

# Commands to run as a job
# Typical use: make use of resources, such as running training.
run: |
  echo "Hello, SkyPilot!"
  conda env list
```

This task can be launched on the cloud with the `sky launch` command.
```bash
$ sky launch my-task.yaml
```
SkyPilot will perform multiple functions for you:
1. Find the lowest priced VM instance type across different clouds
2. Provision the VM
3. Copy the local contents of `workdir` to the VM
4. Run the task's `setup` commands to prepare the VM for running the task 
5. Run the task's `run` commands

<!---- TODO(romilb): Example GIF goes here ---->
Please refer to the [quick start](https://skypilot.readthedocs.io/en/latest/getting-started/quickstart.html) and [documentation](https://skypilot.readthedocs.io/en/latest/) for more on how to use SkyPilot.

## Contributing
Please refer to the [contribution guide](CONTRIBUTING.md).
