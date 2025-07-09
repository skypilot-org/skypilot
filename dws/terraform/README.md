# SkyPilot GKE Cluster Setup

This Terraform configuration creates a Google Kubernetes Engine (GKE) cluster optimized for running SkyPilot workloads with GPU support.

## Prerequisites

1. **GCP Project**: You need an active GCP project with billing enabled
2. **Terraform**: Install Terraform >= 1.0
3. **gcloud CLI**: Install and authenticate with your GCP project
4. **Required APIs**: Enable the following APIs in your project:
   ```bash
   gcloud services enable container.googleapis.com
   gcloud services enable compute.googleapis.com
   gcloud services enable cloudkms.googleapis.com
   ```
5. **Service Account**: The configuration uses the default compute service account which is automatically created in all GCP projects
6. **KMS Encryption**: The cluster uses KMS encryption for etcd. The configuration automatically grants the required permissions to the GKE service account

## Key Features

- **GPU Support**: Configured for NVIDIA H200 GPUs on A3 Ultra instances
- **Queued Provisioning**: Supports Kueue for job scheduling (optional)
- **Flex Start**: Uses flex start instances for cost optimization
- **Private Cluster**: Nodes are in a private network
- **Monitoring**: Full monitoring and logging enabled

## Setup Instructions

1. **Clone and Navigate**:
   ```bash
   cd demo/hippocratic/terraform/
   ```

2. **Configure Variables**:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your project details
   ```

3. **Authenticate with GCP**:
   ```bash
   gcloud auth application-default login
   gcloud config set project YOUR_PROJECT_ID
   ```

4. **Initialize Terraform**:
   ```bash
   terraform init
   ```

5. **Plan and Apply**:
   ```bash
   terraform plan
   terraform apply
   ```

## Configuration Options

### Required Variables
- `project_id`: Your GCP project ID

### Optional Variables
- `region`: GCP region (default: "us-east4")
- `cluster_name`: Name of the GKE cluster (default: "skypilot-gke")
- `vpc_name`: VPC network name (default: "default")
- `subnet_name`: Subnet name (default: "default")

### GPU Availability
The configuration automatically detects zones where A3 Ultra instances with H200 GPUs are available. If no zones are available in your selected region, consider:
- Changing the region
- Requesting quota for A3 instances
- Modifying the machine type to a3-megagpu-8g or other available types

### Disabling KMS Encryption (Optional)
If you don't want KMS encryption, you can disable it by:
1. Removing the `database_encryption` block from the cluster resource
2. Removing the KMS key ring, crypto key, and IAM binding resources
3. Removing the `depends_on` reference in the cluster resource

## Node Pools

1. **Main Pool**: CPU-only nodes (e2-standard-8) for system workloads
2. **GPU Pool with Kueue**: GPU nodes with queued provisioning (requires Kueue installation)
3. **GPU Pool Standard**: GPU nodes with standard provisioning

## Post-Deployment

1. **Get Cluster Credentials**:
   ```bash
   gcloud container clusters get-credentials skypilot-gke --region us-east4
   ```

2. **Install Kueue** (if using queued provisioning):
   ```bash
   kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/v0.9.1/manifests.yaml
   ```

3. **Verify Installation**:
   ```bash
   kubectl get nodes
   kubectl get pods -n kube-system
   ```

## Cost Considerations

- **A3 Ultra instances** are expensive ($30+ per hour per node)
- **Flex Start** helps reduce costs by allowing preemptible scheduling
- **Autoscaling** ensures nodes are only created when needed
- Consider using **spot instances** for development/testing

## Troubleshooting

### Common Issues

1. **Quota Limits**: A3 instances require special quota approval
2. **Region Availability**: H200 GPUs are not available in all regions
3. **IAM Permissions**: Ensure your account has sufficient permissions
4. **Service Account Errors**: If you see service account not found errors, the configuration automatically uses the default compute service account

### Useful Commands

```bash
# Check node pool status
kubectl get nodes -l cloud.google.com/gke-nodepool

# View GPU resources
kubectl describe nodes -l skypilot.co/accelerator=H200

# Check Kueue installation
kubectl get crd | grep kueue
```

## Cleanup

To destroy the infrastructure:
```bash
terraform destroy
```

**Warning**: This will delete the entire cluster and all workloads running on it. 