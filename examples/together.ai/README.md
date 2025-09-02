# Orchestrate AI workloads on Together.ai with SkyPilot

[Together.ai](https://www.together.ai/) is a cloud platform that offers GPU clusters that can be used for running AI workloads.

This example shows how to use Together.ai with SkyPilot and run the `gpt-oss-20b` model for finetuning on it.

## Prerequisites

- Install SkyPilot

```bash
uv pip install skypilot[kubernetes]
```

## Launch a Together.ai GPU cluster

1. Create a Together.ai GPU cluster with Cluster type selected as Kubernetes
   ![](https://i.imgur.com/E9m0wEV.png)

2. Get the Kubernetes config for the cluster
   ![](https://i.imgur.com/90zxTXE.png)

3. Save the Kubernetes config to a file say `./together-kubeconfig`
4. Copy the kubeconfig to your `~/.kube/config` or merge the Kubernetes config with your existing kubeconfig file.
   ```bash
   mkdir -p ~/.kube
   cp together-kubeconfig ~/.kube/config
   ```
   or
   ```bash
   KUBECONFIG=./together-kubeconfig:~/.kube/config kubectl config view --flatten > /tmp/merged_kubeconfig && mv /tmp/merged_kubeconfig ~/.kube/config    
   ```


## Use the Together.ai cluster with SkyPilot

1. Check that SkyPilot can access the Together.ai cluster
   ```bash
   sky check k8s
   Checking credentials to enable infra for SkyPilot.
     Kubernetes: enabled [compute]
       Allowed contexts:
       └── t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6: enabled.
   
   🎉 Enabled infra 🎉
     Kubernetes [compute]
       Allowed contexts:
       └── t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6
   
   To enable a cloud, follow the hints above and rerun: sky check
   If any problems remain, refer to detailed docs at: https://docs.skypilot.co/en/latest/getting-started/installation.html
   ```
   Your Together.ai cluster is now accessible with SkyPilot.

2. Check the available GPUs on the cluster:
   ```bash
   sky show-gpus --infra k8s
   Kubernetes GPUs
   Context: t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6
   GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION  
   H100  1, 2, 4, 8                8 of 8 free  
   Kubernetes per-node GPU availability
   CONTEXT                                                                              NODE                GPU   UTILIZATION  
   t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6  cp-8ct86            -     0 of 0 free  
   t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6  cp-fjqbt            -     0 of 0 free  
   t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6  cp-hst5f            -     0 of 0 free  
   t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6-admin@t-51326e6b-25ec-42dd-8077-6f3c9b9a34c6  gpu-dp-gsd6b-k4m4x  H100  8 of 8 free  
   ```


## Example: Finetuning gpt-oss-20b on the Together.ai cluster

Launch a gpt-oss finetuning job on the Together.ai cluster is now as simple as a single command:
```bash
sky launch -c gpt-together gpt-oss-20b.yaml
```

![](https://i.imgur.com/RMpEyjR.png)
