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
```

## Running a local development cluster
We use (kind)[https://kind.sigs.k8s.io/] to run a local Kubernetes cluster 
for development.

```bash 
sky local up
```

## Running a GKE cluster
1. Make sure ports 30000-32767 are open in your node pool VPC's firewall.
2. Create a GKE cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.
   * Note - only GKE standard clusters are supported. GKE autopilot clusters are not supported.
3. Get the kubeconfig for your cluster and place it in `~/.kube/config`:
```bash
gcloud container clusters get-credentials <cluster-name> --region <region>
# Example:
# gcloud container clusters get-credentials testcluster --region us-central1-c
```
4. Verify by running `kubectl get nodes`. You should see your nodes.
5. You can run SkyPilot tasks now. 

## Other useful scripts
`scripts` directory contains other useful scripts for development, including 
Kubernetes dashboard, ray yaml for testing the SkyPilot Kubernetes node provider 
and more.