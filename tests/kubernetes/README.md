# SkyPilot Kubernetes Development Scripts

This directory contains useful scripts and notes for developing SkyPilot on Kubernetes. 

## Building and pushing SkyPilot image

We maintain a container image that has all basic SkyPilot dependencies installed. 
This image is hosted at `us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest`.

To build this image locally and optionally push to the SkyPilot registry, run:
```bash
# Build and load image locally
./build_image.sh
# Build and push image (CAREFUL - this will push to the SkyPilot registry!)
./build_image.sh -p
# Build and push GPU image (CAREFUL - this will push to the SkyPilot registry!)
./build_image.sh -p -g
```

## Running a local development cluster
We use (kind)[https://kind.sigs.k8s.io/] to run a local Kubernetes cluster 
for development. To create a local development cluster, run:

```bash 
sky local up
```

## Running a GKE cluster
1. Create a GKE cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.
   * Note - only GKE standard clusters are supported. GKE autopilot clusters are not supported.
   * Tip - to create an example GPU cluster for testing, this command will create a 2 node cluster with 1x T4 and another with 1x V100:
     ```bash
      PROJECT_ID=$(gcloud config get-value project)
      CLUSTER_NAME=testclusterromil
      gcloud beta container --project "${PROJECT_ID}" clusters create "${CLUSTER_NAME}" --zone "us-central1-c" --no-enable-basic-auth --cluster-version "1.27.3-gke.100" --release-channel "regular" --machine-type "n1-standard-8" --accelerator "type=nvidia-tesla-k80,count=1" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "1" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/${PROJECT_ID}/global/networks/default" --subnetwork "projects/${PROJECT_ID}/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c" && gcloud beta container --project "${PROJECT_ID}" node-pools create "v100" --cluster "${CLUSTER_NAME}" --zone "us-central1-c" --machine-type "n1-standard-12" --accelerator "type=nvidia-tesla-v100,count=1" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "1" --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --node-locations "us-central1-c"
      ```
2. Make sure ports 30000-32767 are open in your node pool VPC's firewall.
3. Get the kubeconfig for your cluster and place it in `~/.kube/config`:
   ```bash
   gcloud container clusters get-credentials <cluster-name> --region <region>
   # Example:
   # gcloud container clusters get-credentials testcluster --region us-central1-c
   ```
4. Verify by running `kubectl get nodes`. You should see your nodes.
5. **If you want GPU support**, make sure you install GPU drivers by running:
   ```bash
   # If using COS based nodes (e.g., in the example above):
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
   
   # If using Ubuntu based nodes:
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded.yaml
   ```
   This will create a resource like `nvidia.com/gpu: 1`. You can verify this resource is available by running:
   ```bash
   kubectl describe nodes
   ```
6. Run `sky check`.
   ```bash
   sky check
   ```

7. You can run SkyPilot tasks now. 

## Other useful scripts
`scripts` directory contains other useful scripts for development, including 
Kubernetes dashboard, ray yaml for testing the SkyPilot Kubernetes node provider 
and more.
