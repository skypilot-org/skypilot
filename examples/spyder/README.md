# Connect Spyder IDE to a SkyPilot Cluster and develop your R/Python program

SkyPilot supports interactive development on any infra, including cloud VMs, and even Kubernetes. You can SSH into the SkyPilot cluster or connect your IDE with it, e.g., VSCode, or Spyder.


We will showcase how to connect Spyder to a SkyPilot cluster in this example, where we utilize the remote kernel feature of Spyder to run your code on a remote SkyPilot cluster. This can help you get high-end resources and run your code on it super easily.

## Step 1: Launch the cluster ready for Spyder

Launching the Spyder-ready resources requires only one command:

```bash
sky launch -c my-spyder spyder.yaml --detach-run
```

Note that, the command above will launch a SkyPilot cluster with 8 CPUs and 16GB memory, but you can override the resources:

```bash
# Get a large cluster with 16 CPUs and 64GB memory, and 1 H100 GPU
sky launch -c my-spyder spyder.yaml --detach-run --cpus 16 --memory 64+ --gpus H100:1
```

## Step 2: Connect your Spyder to the SkyPilot cluster

Now, you have a SkyPilot cluster ready with Spyder kernel running on it.

To connect your Spyder to the SkyPilot cluster, you need to download the connection file from the cluster:
```console
# Download the connection file
scp my-spyder:~/.local/share/jupyter/runtime/kernel-*.json .
```

With the connection file, you now setup the connection as the following:

a. Click on `Consoles` on the top bar, and select `Connect to an existing kernel`.
  ![](https://i.imgur.com/nUCwxxK.png)
b. In the window, select the connection file downloaded, and specify the cluster name, `my-spyder` in the Hostname field, and Username as `sky` (required by Spyder).
  ![](https://i.imgur.com/83Q0PYR.png)
c. Click on OK, and you will be connected to the SkyPilot cluster, and you can run your script through the console
  ![](https://i.imgur.com/2lePAxj.png)

Note: username need to be specified explicitly as `sky` due to the limitation of Spyder.
