
readme_content = """# Enabling AMD GPU Detection and Scheduling in SkyPilot on Kubernetes

We’ve successfully enabled AMD GPU detection and scheduling in SkyPilot on Kubernetes! 

To support AMD GPUs over k8s in SkyPilot (GPU detection), we propose the following steps:

**Testing Infrastructure:** 

- **Hardware**: 8xMI300 AMD GPUs
- **Software**: microk8s, Helm 3, SkyPilot.
- **GPU Operator**: AMD ROCm GPU operator v1.3.0

### Installation steps

**Install Helm** 

```
helm repo add jetstack https://charts.jetstack.io --force-update
helm install cert-manager jetstack/cert-manager \
          		--namespace cert-manager \
          		--create-namespace \
          		--version v1.15.1 \
          		--set crds.enabled=true
```          

**Add AMD GPU operator Helm repo**
```
helm repo add rocm https://rocm.github.io/gpu-operator
helm repo update
``` 

**Install AMD GPU operator**
```
helm helm install amd-gpu-operator rocm/gpu-operator-charts \
		  --namespace kube-amd-gpu --create-namespace \
		  --version=v1.3.0
``` 

### GPU Detection and Labeling

**Check for AMD GPU Labels**
```
kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}' | grep -e "amd.com/gpu" 
``` 

**Check Node Capacity**
```
kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, capacity: .status.capacity}'
``` 

_Sample output:_

```
{
  "name": "<node-name>",
  "capacity": {
      		    "amd.com/gpu": "8",
      		    "cpu": "160",
      		    "ephemeral-storage": "2077109340Ki",
      		    "hugepages-1Gi": "0",
      		    "hugepages-2Mi": "0",
      		    "memory": "1981633424Ki",
      		    "pods": "110"
  		  }
}
``` 


### Label nodes for SkyPilot

```
kubectl label node <node-name> skypilot.co/accelerator=mi300
``` 

**Verify Labels:**

```
kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}' | grep -e "amd.com/gpu" -e "mi300"
``` 

###  SkyPilot Integration

**Check for Kubernetes and GPU Detection**
```
 sky check
```
**List Available Accelerators (AMD GPUs)**

```
sky show-gpus --infra k8s
``` 		
_Sample output:_

```

Kubernetes GPUs
Context: microk8s
GPU    REQUESTABLE_QTY_PER_NODE  UTILIZATION  
MI300  1, 2, 4, 8                6 of 8 free  
Kubernetes per-node GPU availability
CONTEXT   NODE         GPU    UTILIZATION  
microk8s  mi300-8gpus  MI300  6 of 8 free  
``` 

**Run a sample example with AMD Dockers**
1. Smoke test: 
```
 sky launch -c amd-cluster examples/amd/amd_smoke_test.yaml
```
2. Pytorch example Reinforcement learning:
```
 sky launch -c amd-cluster examples/amd/amd_pytorch_RL.yaml
```

This setup demonstrates that SkyPilot can now:

- Detect AMD GPUs via amd.com/gpu labels
- Schedule jobs using skypilot.co/accelerator=mi300

We hope this contribution helps move forward **official AMD GPU support** in SkyPilot. 
