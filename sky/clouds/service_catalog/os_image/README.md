# Custom OS Image Generation for SkyPilot

## GCP
The following is an example of generating a custom skypilot image for GPU instance running Debian 11 OS.
1. Use SkyPilot to launch a VM \
<!-- `sky launch sky/clouds/service_catalog/os_image/skypilot-gcp-gpu-debian-11.yaml` -->
```bash
CLUSTER_NAME="image-creation"
ZONE="us-west1-a"
sky launch -c $CLUSTER_NAME --zone $ZONE sky/clouds/service_catalog/os_image/skypilot-gcp-gpu-debian-11.yaml
```

2. Stop the VM, create a custom image and make it public \
```
sky stop $CLUSTER_NAME -y
IMAGE_NAME="skypilot-gcp-gpu-debian-11-v$(date +'%y%m%d')"
VM_BOOT_DISK_NAME=$(gcloud compute instances list --filter="labels.skypilot-cluster-name=$CLUSTER_NAME" --format="value(name)")
gcloud compute images create ${IMAGE_NAME} \
  --source-disk=${VM_BOOT_DISK_NAME} \
  --source-disk-zone=${ZONE} \
  --family=skypilot-gpu \
  --storage-location=us
gcloud compute images add-iam-policy-binding ${IMAGE_NAME} --member='allAuthenticatedUsers' --role='roles/compute.imageUser'
echo "Image ID projects/sky-dev-465/global/images/${IMAGE_NAME} is created and public."
```

3. [Optional] Test the image: change default image ID in `sky/clouds/gcp.py` to the new image ID and run \
```bash
pytest tests/test_smoke.py::test_huggingface --gcp
```

4. Clean up: delete deprecated images and stop sky cluster \
`sky down -y ${CLUSTER_NAME}`

5. Create a PR to update SkyPilot Catalog's latest version of [`images.csv`](https://github.com/skypilot-org/skypilot-catalog/blob/master/catalogs/v5/gcp/images.csv): update row for tag `skypilot:custom-gpu-debian-11`.
