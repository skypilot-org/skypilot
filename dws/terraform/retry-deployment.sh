#!/bin/bash

echo "Cleaning up failed resources..."
terraform destroy -target=google_container_cluster.gke -auto-approve || true
terraform destroy -target=google_kms_crypto_key.gke_key -auto-approve || true
terraform destroy -target=google_kms_key_ring.gke_keyring -auto-approve || true

echo "Retrying deployment with fixed configuration..."
terraform apply

echo "Deployment complete! Next steps:"
echo "1. Get cluster credentials:"
echo "   gcloud container clusters get-credentials skypilot-dws --region us-east4"
echo ""
echo "2. Verify cluster is working:"
echo "   kubectl get nodes"
echo ""
echo "3. Install Kueue (if using queued provisioning):"
echo "   kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/v0.9.1/manifests.yaml" 