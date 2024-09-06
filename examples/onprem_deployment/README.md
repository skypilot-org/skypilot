# Deploy SkyPilot on your own infrastructure

This guide will help you deploy SkyPilot on your own infrastructure - whether it's on-premises or in the cloud as reserved instances.

Given a list of IP addresses and SSH keys, the `deploy.sh` script will deploy Kubernetes on those machines and 
connect your SkyPilot installation to the cluster.

At the end of this guide, you will be able to run SkyPilot clusters, jobs and services on your machines, even if they were not provisioned with SkyPilot.

## Prerequisites
**Local machine (typically your laptop):**
* [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl/)
* [SkyPilot](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)

**Remote machines (machines in your cluster, optionally with GPUs):**
* Debian-based OS (tested on Debian 11)
* SSH access with key-based authentication
  * All machines must use the same SSH key and username 
* Port 6443 must be accessible on the head node from your local machine

## Guide

1. Create `ips.txt` with the IP addresses of your machines with one IP per line (like a MPI hostfile). 
The first node will be used as the head node. Here is an example `ips.txt` file:

    ```
    192.168.1.1
    192.168.1.2
    192.168.1.3
    ```

2. Run `./deploy.sh` and pass the ips.txt file, SSH username and SSH keys as arguments:

    ```bash
    chmod +x deploy.sh
    IP_FILE=ips.txt
    USERNAME=username
    SSH_KEY=path/to/ssh/key
    ./deploy.sh $IP_FILE $USERNAME $SSH_KEY
    ```

3. The script will deploy a Kubernetes cluster on the remote machines, setup GPU support, setup Kubernetes credentials on your local machine and setup SkyPilot to operate with the new cluster.

   At the end, you should see a message like this:

    ```
    ✔ SkyPilot configured successfully.
    ==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ====
    ```

4. To verify that the cluster is running, run:

    ```bash
    sky check kubernetes
    ```
   
    You can now use SkyPilot to launch your development clusters and training jobs on your own infrastructure.

    ```console
    $ sky show-gpus --cloud kubernetes
    Kubernetes GPUs
    GPU   QTY_PER_NODE  TOTAL_GPUS  TOTAL_FREE_GPUS
    L4    1, 2, 4       12          2
    H100  1, 2, 4, 8    16          12
    Kubernetes per node GPU availability
    NODE_NAME                  GPU_NAME  TOTAL_GPUS  FREE_GPUS
    gke-inference-pool-0       L4        4           2
    gke-inference-pool-1       L4        4           0
    gke-inference-pool-2       L4        2           0
    gke-inference-pool-3       L4        2           0
    gke-training-pool-0        H100      8           8
    gke-training-pool-1        H100      8           4
    ```
   
    You can also use `kubectl` to interact with the cluster.

5. To teardown the Kubernetes and clean up, use the `--cleanup` flag:

    ```bash
    IP_FILE=ips.txt
    USERNAME=username
    SSH_KEY=path/to/ssh/key
    ./deploy.sh $IP_FILE $USERNAME $SSH_KEY --cleanup
    ```
   
    This will stop all Kubernetes services from the remote machines.