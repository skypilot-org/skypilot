# Deploying a Kubernetes cluster on Lambda in 1-click with SkyPilot

This example demonstrates how to deploy a Kubernetes cluster on Lambda instances.

## Prerequisites
1. SkyPilot installed from `lambda_k8s` branch:
```bash
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot

git checkout lambda_k8s
pip install -e .[lambda]
```

2. On your lambda cloud dashboard, configure the firewall to allow inbound connections on port `443` and `6443` (required to expose k8s API server).

<p align="center">
<img src="https://i.imgur.com/uSA7BMH.png" alt="firewall" width="500"/>
</p>

## Instructions

1. Edit `deploy_k8s.yaml` to set the desired number of workers and GPUs per node.
```yaml
resources:
  cloud: lambda
  accelerators: A10:1

num_nodes: 2
```

2. Use the convenience script to launch the cluster:
```bash
./launch_k8s.sh
```

SkyPilot will do all the heavy lifting for you: provision lambda VMs, deploy the k8s cluster, fetch the kubeconfig, and set up your local kubectl to connect to the cluster.

3. You should now be able to run `kubectl` and `sky` commands to interact with the cluster:
```console
$ kubectl get nodes
NAME              STATUS   ROLES                  AGE   VERSION
129-80-133-44     Ready    <none>                 14m   v1.30.4+k3s1
150-230-191-161   Ready    control-plane,master   14m   v1.30.4+k3s1

$ sky show-gpus --cloud kubernetes
Kubernetes GPUs
GPU  QTY_PER_NODE  TOTAL_GPUS  TOTAL_FREE_GPUS
A10  1             2           2              

Kubernetes per node GPU availability
NODE_NAME        GPU_NAME  TOTAL_GPUS  FREE_GPUS
129-80-133-44    A10       1           1
150-230-191-161  A10       1           1
```

4. To tear down the cluster, run:
```bash
sky down k8s
```


