# Multi-cluster sharing in SkyPilot with priorities and preemption

This example shows how to use SkyPilot to share clusters across organizations, while still 
retaining the ability to prioritize your own workloads on your own clusters.

In this example, we have two organizations, `rise` and `bair`. Each organization  
has its own kubernetes cluster, and each organization has a user submitting their workloads.

## Setup
1. Create two Kubernetes clusters, each having one node with 2x T4 GPUs:
```console
PROJECT_ID=$(gcloud config get-value project)
CLUSTER_NAME=bair
gcloud beta container --project "${PROJECT_ID}" clusters create "${CLUSTER_NAME}" --zone "us-central1-c" --no-enable-basic-auth --cluster-version "1.27.3-gke.100" --release-channel "regular" --machine-type "n1-standard-8" --accelerator "type=nvidia-tesla-t4,count=2" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "1" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/${PROJECT_ID}/global/networks/default" --subnetwork "projects/${PROJECT_ID}/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c"
CLUSTER_NAME=rise
gcloud beta container --project "${PROJECT_ID}" clusters create "${CLUSTER_NAME}" --zone "us-central1-c" --no-enable-basic-auth --cluster-version "1.27.3-gke.100" --release-channel "regular" --machine-type "n1-standard-8" --accelerator "type=nvidia-tesla-t4,count=2" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "1" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/${PROJECT_ID}/global/networks/default" --subnetwork "projects/${PROJECT_ID}/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c"
```

2. Update your kubeconfig context names to use simple names ('rise', 'bair'), instead of GKE generated names.
```console
kubectl config rename-context gke_${PROJECT_ID}_us-central1-c_rise rise
kubectl config rename-context gke_${PROJECT_ID}_us-central1-c_bair bair
```

## Running the example
1. Create the priority classes for each cluster:
```console
kubectl apply -f highpriority.yaml --context rise
kubectl apply -f lowpriority.yaml --context rise
kubectl apply -f highpriority.yaml --context bair
kubectl apply -f lowpriority.yaml --context bair
```

2. Update `kubernetes.py` with the kubernetes context names and set the current priorities
3. Run the example as a rise user:
```console
sky launch -c romil --gpus T4:2 -- nvidia-smi # This will get placed on the rise cluster
sky launch -c romil-train --gpus T4:2 -- nvidia-smi # This will get placed on the bair cluster with low priority since rise is full
```
4. To run the example as a bair user, update `kubernetes.py` and swap priorities (set BAIR as high priority and RISE as low priority)
```console
sky launch -c abbeel --gpus T4:2 -- nvidia-smi # This will preempt romil-train and get placed on the bair cluster
```

## Open questions
* Preemption handling by SkyPilot
* Restricting priorities to certain users
* Cluster selection - using cost as proxy?
* UX
