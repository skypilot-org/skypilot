# Deploying a Kubernetes cluster on the cloud in 1-click with SkyPilot

This example demonstrates how to deploy a Kubernetes cluster on the cloud with SkyPilot. For the purposes of this guide, we will use lambda cloud as the cloud provider, but you can change cloud providers by editing `cloud_k8s.yaml`.

## Prerequisites
1. Latest SkyPilot nightly release:
```bash
pip install "skypilot-nightly[lambda,kubernetes]"
```

2. Use a cloud which supports opening ports on SkyPilot or manually expose ports 6443 and 443 on the VMs. This is required to expose k8s API server. 

   For example, if using lambda cloud, configure the firewall on the lambda cloud dashboard to allow inbound connections on port `443` and `6443`.

<p align="center">
<img src="https://i.imgur.com/uSA7BMH.png" alt="firewall" width="500"/>
</p>

## Instructions

1. Edit `cloud_k8s.yaml` to set the desired number of workers and GPUs per node. If using GCP, AWS or Azure, uncomment the ports line to allow inbound connections to the Kubernetes API server. 
```yaml
resources:
  infra: lambda
  accelerators: A10:1
  # ports: 6443

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
GPU  REQUESTABLE_QTY_PER_NODE  UTILIZATION
A10  1                         2 of 2 free

Kubernetes per node GPU availability
NODE             GPU       UTILIZATION
129-80-133-44    A10       1 of 1 free
150-230-191-161  A10       1 of 1 free
```

## Run AI workloads on your Kubernetes cluster with SkyPilot

### Development clusters
To launch a [GPU enabled development cluster](https://docs.skypilot.co/en/latest/examples/interactive-development.html), run `sky launch -c mycluster --cloud kubernetes --gpus A10:1`. 

SkyPilot will setup SSH config for you.
* [SSH access](https://docs.skypilot.co/en/latest/examples/interactive-development.html#ssh): `ssh mycluster`
* [VSCode remote development](https://docs.skypilot.co/en/latest/examples/interactive-development.html#vscode): `code --folder-uri "vscode-remote://ssh-remote+mycluster/home/sky"`


### Jobs
To run jobs, use `sky jobs launch --gpus A10:1 --cloud kubernetes -- 'nvidia-smi; sleep 600'`

You can submit multiple jobs and let SkyPilot handle queuing if the cluster runs out of resources:
```bash
$ sky jobs queue
Fetching managed job statuses...
Managed jobs
In progress tasks: 2 RUNNING, 1 STARTING
ID  TASK  NAME      RESOURCES  SUBMITTED    TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
3   -     finetune  1x[A10:1]  24 secs ago  24s            -             0            STARTING
2   -     qlora     1x[A10:1]  2 min ago    2m 18s         12s           0            RUNNING
1   -     sky-cmd   1x[A10:1]  4 mins ago   4m 27s         3m 12s        0            RUNNING
```

You can also observe the pods created by SkyPilot with `kubectl get pods`:
```bash
$ kubectl get pods
NAME                                     READY   STATUS    RESTARTS   AGE
qlora-2-2ea4-head                        1/1     Running   0          5m31s
sky-cmd-1-2ea4-head                      1/1     Running   0          8m36s
sky-jobs-controller-2ea485ea-2ea4-head   1/1     Running   0          10m
```

Refer to [SkyPilot docs](https://docs.skypilot.co/) for more.

## Teardown
To teardown the Kubernetes cluster, run:
```bash
sky down k8s
```
