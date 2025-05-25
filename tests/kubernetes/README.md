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

### Mocking a GPU node on your `sky local up` cluster

To mock a GPU node on your local cluster, you can add a label and a nvidia.com/gpu virtual resource to a node.

```bash
# Make sure `sky local up` cluster is running
kubectl label node skypilot-control-plane skypilot.co/accelerator=h100 # Or any other GPU. Be sure to use lowercase!

# Add resource. Run proxy in a terminal window:
kubectl proxy
# In a new terminal, run
curl --header "Content-Type: application/json-patch+json" \
  --request PATCH \
  --data '[{"op": "add", "path": "/status/capacity/nvidia.com~1gpu", "value": "8"}]' \
  http://localhost:8001/api/v1/nodes/skypilot-control-plane/status
```


## Running a GKE cluster
1. Create a GKE cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.
   * Note - only GKE standard clusters are supported. GKE autopilot clusters are not supported.
   * Tip - to create an example GPU cluster for testing, this command will create a 6 node cluster with 2x T4-8cpu, 2x V100-8cpu and 2x 16cpu CPU-only node:
   ```bash
   PROJECT_ID=$(gcloud config get-value project)
   CLUSTER_NAME=skypilot-test-cluster
   REGION=us-central1-c
   GKE_VERSION=$(gcloud container get-server-config \
   --region=${REGION} \
   --flatten=channels \
   --filter="channels.channel=REGULAR" \
   --format="value(channels.defaultVersion)")
   # Common arguments for both cluster and node pools
   ARGS="--project ${PROJECT_ID} \
   --zone ${REGION} \
   --machine-type n1-standard-8 \
   --image-type COS_CONTAINERD \
   --disk-type pd-balanced \
   --disk-size 100 \
   --metadata disable-legacy-endpoints=true \
   --scopes https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append \
   --num-nodes 2 \
   --enable-autoupgrade \
   --enable-autorepair \
   --max-surge-upgrade 1 \
   --max-unavailable-upgrade 0 \
   --node-locations ${REGION}"

   # Create cluster
   gcloud beta container clusters create ${CLUSTER_NAME} ${ARGS} \
   --cluster-version ${GKE_VERSION} \
   --release-channel "regular" \
   --no-enable-basic-auth \
   --logging=SYSTEM,WORKLOAD \
   --monitoring=SYSTEM \
   --enable-ip-alias \
   --network "projects/${PROJECT_ID}/global/networks/default" \
   --subnetwork "projects/${PROJECT_ID}/regions/${REGION%-*}/subnetworks/default" \
   --no-enable-intra-node-visibility \
   --default-max-pods-per-node "110" \
   --security-posture=standard \
   --workload-vulnerability-scanning=disabled \
   --no-enable-master-authorized-networks \
   --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver \
   --enable-managed-prometheus \
   --enable-shielded-nodes

   # Create node pools
   gcloud beta container node-pools create "spot-t4" ${ARGS} \
   --cluster ${CLUSTER_NAME} --spot \
   --accelerator "type=nvidia-tesla-t4,count=1"

   gcloud beta container node-pools create "t4" ${ARGS} \
   --cluster ${CLUSTER_NAME} --accelerator "type=nvidia-tesla-t4,count=1"

   gcloud beta container node-pools create "v100" ${ARGS} \
   --cluster ${CLUSTER_NAME} --accelerator "type=nvidia-tesla-v100,count=1"

   gcloud beta container node-pools create "l4" ${ARGS} \
   --cluster ${CLUSTER_NAME} --machine-type "g2-standard-4" \
   --accelerator "type=nvidia-l4,count=1"

   gcloud beta container node-pools create "largecpu" ${ARGS} \
   --cluster ${CLUSTER_NAME} --machine-type "n1-standard-16"
   ```

2. Get the kubeconfig for your cluster and place it in `~/.kube/config`:
   ```bash
   gcloud container clusters get-credentials <cluster-name> --region <region>
   # Example:
   # gcloud container clusters get-credentials $CLUSTER_NAME --region us-central1-c
   ```
3. Verify by running `kubectl get nodes`. You should see your nodes.
4. **If you want GPU support**, make sure you install GPU drivers by running:
   ```bash
   # If using COS based nodes (e.g., in the example above):
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml

   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded-latest.yaml

   # If using Ubuntu based nodes:
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded.yaml

   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded-R525.yaml
   ```
   This will create a resource like `nvidia.com/gpu: 1`. You can verify this resource is available by running:
   ```bash
   kubectl describe nodes
   ```
5. Run `sky check`.
   ```bash
   sky check
   ```

6. You can run SkyPilot tasks now. After you're done, delete the cluster by running:
   ```bash
   gcloud container clusters delete <cluster-name> --region <region>
   # Example:
   # gcloud container clusters delete testcluster --region us-central1-c
   ```
NOTE - If are using nodeport networking, make sure port 32100 is open in your node pool VPC's firewall.

To delete the node pools, run:
```bash
gcloud container node-pools delete "spot-t4" --region ${REGION} --cluster ${CLUSTER_NAME} --async
gcloud container node-pools delete "t4" --region ${REGION} --cluster ${CLUSTER_NAME} --async
gcloud container node-pools delete "v100" --region ${REGION} --cluster ${CLUSTER_NAME} --async
gcloud container node-pools delete "l4" --region ${REGION} --cluster ${CLUSTER_NAME} --async
gcloud container node-pools delete "largecpu" --region ${REGION} --cluster ${CLUSTER_NAME} --async
```

## Running a EKS cluster
1. Create a EKS cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.
   * Tip - to create an example GPU cluster for testing, this command will create a 3 node cluster with 1x T4-8cpu, 1x V100-8cpu and 1x 16cpu CPU-only node. It will also automatically update your kubeconfig file:
     ```bash
     eksctl create cluster -f tests/kubernetes/eks_test_cluster.yaml
     ```
2. Verify by running `kubectl get nodes`. You should see your nodes.
3. **If you want GPU support**, EKS clusters already come with GPU drivers setup. However, you'll need to label the nodes with the GPU type. Use the SkyPilot node labelling tool to do so:
   ```bash
   python -m sky.utils.kubernetes.gpu_labeler
   ```
   This will create a job on each node to read the GPU type from `nvidia-smi` and assign the label to the node. You can check the status of these jobs by running:
   ```bash
   kubectl get jobs -n kube-system
   ```
   After the jobs are done, you can verify the GPU labels are setup correctly by looking for `skypilot.co/accelerator` label in the output of:
   ```bash
   kubectl describe nodes
   ```
   In case something goes wrong, you can clean up these jobs by running:
   ```bash
   python -m sky.utils.kubernetes.gpu_labeler --cleanup
   ```
5. Run `sky check`.
   ```bash
   sky check
   ```
5. After you are done, delete the cluster by running:
   ```bash
   eksctl delete cluster -f tests/kubernetes/eks_test_cluster.yaml
   ```

NOTE - If are using nodeport networking, make sure port 32100 is open in your EKS cluster's default security group.

## Other useful scripts
`scripts` directory contains other useful scripts for development, including
Kubernetes dashboard, ray yaml for testing the SkyPilot Kubernetes node provider
and more.
