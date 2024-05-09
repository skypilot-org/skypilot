# Using SkyPilot with EKS and Karpenter
This guide will set up SkyPilot to work with an EKS cluster that optionally uses Karpenter for autoscaling.

## Prerequisites
- An EKS cluster, optionally with Karpenter installed.
  - If you don't have one, follow the [EKS + Karpenter guide](deploy_karpenter.md) first to set up a cluster.

## Setup SkyPilot to work with EKS

Setup your EKS cluster to work with SkyPilot by following the [SkyPilot on Kubernetes docs](https://skypilot.readthedocs.io/en/latest/reference/kubernetes/index.html#submitting-skypilot-tasks-to-kubernetes-clusters). For convenience, we will list down the steps here.

0. [Install SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) from pypi nightly build or from source.
    - Installing nightly build from pip - `pip install -U "skypilot-nightly[aws,gcp,kubernetes]==1.0.0.dev20240510"`. 
      - Available on PyPI on May 10th 2024.
    - Installing from source - `git clone https://github.com/skypilot-org/skypilot.git && cd skypilot && pip install -e ".[aws,gcp,kubernetes]"`
      - Make sure to use [commit `4683c46`](https://github.com/skypilot-org/skypilot/commit/4683c46edb5e1b5dcadb1ef143e6baedc145fcd4) or later.


1. Make sure kubectl, socat and nc (netcat) are installed on your local machine.
    ```bash
    # MacOS
    brew install kubectl socat netcat
    
    # Linux (may have socat already installed)
    sudo apt-get install kubectl socat netcat
    ```

2. Place your kubeconfig file at `~/.kube/config` and has the correct context configured. You can verify by running `kubectl get pods`.
    ```bash
    mkdir -p ~/.kube
    cp /path/to/kubeconfig ~/.kube/config
    ```

3. Run `sky check` and verify that Kubernetes is enabled in SkyPilot.
    ```bash
    $ sky check
    
    Checking credentials to enable clouds for SkyPilot.
    ...
    Kubernetes: enabled
    ...
    ```

4. **If you are using an autoscaling cluster with Karpenter**, configure your `~/.sky/config.yaml` with this command:
    ```bash
    mkdir -p ~/.sky
    cat <<EOF > ~/.sky/config.yaml
    kubernetes:
      autoscaler: karpenter
      provision_timeout: 900
    EOF
    ```
    This configures SkyPilot to use Karpenter for autoscaling and sets a long provision timeout (15 minutes) to allow Karpenter to scale up the nodes while SkyPilot waits. 

    If Karpenter is unable to provision the resources within this time, SkyPilot will spill your job to the next cheapest and available cloud.

5. **If you are not using Karpenter** and already have GPU nodes in your cluster, you will need to [label](https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html#setting-up-gpu-support) your nodes with the `skypilot.co/accelerator: <gpu-name>` label to indicate which GPU type is available on the cluster.
   
   We provide a helper command to label the nodes with the GPU type:
    ```bash
    python -m sky.utils.kubernetes.gpu_labeler
    ```
   
   You can find more details [here](https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html#deploying-on-amazon-eks).

6. Run `sky show-gpus --cloud kubernetes` to list the GPUs _currently provisioned_ in your cluster. Note that this will not list the GPUs that may be autoscaled and added in the future by Karpenter.
    ```bash
    $ sky show-gpus --cloud kubernetes
    COMMON_GPU  AVAILABLE_QUANTITIES
    V100        1, 2
    T4          1, 2, 4
   
    Note: Kubernetes cluster autoscaling is enabled. All GPUs that can be provisioned may not be listed here. Refer to your autoscaler's node pool configuration to see the list of supported GPUs.
    ``` 

## Running pods for development with SkyPilot

You can provision a pod for interactive development with SSH on your Kubernetes cluster using SkyPilot. Our [docs explain this in detail](https://skypilot.readthedocs.io/en/latest/examples/interactive-development.html), but we will show some examples here.

1. To launch a cluster for development:
    ```bash
    # Launch a cluster with 1 NVIDIA GPU and sync the local working directory to the
    # cluster.
    sky launch -c dev --gpus T4 --workdir .
    ```

2. Connect with ssh and use as a regular development machine:
    ```bash
    ssh dev
    ```

3. Integrate with VSCode:
    ```
    CLUSTER_NAME=dev
    REMOTE_HOME=$(ssh dev 'echo $HOME')
    code --remote ssh-remote+$CLUSTER_NAME $REMOTE_HOME
    ```
<p align="center">
    <img src="https://imgur.com/8mKfsET.gif" width="500px">
</p>

4. Run jupyter notebooks:
    ```bash
    CLUSTER_NAME=dev
    ssh -L 8888:localhost:8888 $CLUSTER_NAME
    
   # In the ssh session, run:
    pip install jupyter
    jupyter notebook
   
    # Now, open your browser and go to http://localhost:8888
    ```
<p align="center">
    <img src="https://skypilot.readthedocs.io/en/latest/_images/jupyter-gpu.png" width="500px">
</p>

5. Automatically terminate the pod/VM when you are done:
    ```bash
    # Auto down the cluster after 5 hours
    sky autostop --down -i 300 dev
    
    # Launch a cluster with auto down after 5 hours
    sky launch -c dev --gpus T4 --workdir . --down -i 300
    ```
