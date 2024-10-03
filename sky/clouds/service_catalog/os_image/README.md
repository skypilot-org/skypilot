# Custom OS Image Generation for SkyPilot

## GCP
The following is an example of generating a custom skypilot image for GPU instance running Debian 11 OS.
1. Use SkyPilot to launch a VM \
`sky launch sky/clouds/os_image/skypilot-gcp-gpu-debian-11.yaml`

2. Stop the VM and create a custom image with name *skypilot-gcp-gpu-debian-11-v{YYMMDD}* \
`gcloud compute images create ${IMAGE_NAME} --source-disk=${VM_BOOT_DISK_NAME} --source-disk-zone=${VM_ZONE} --family=skypilot-gpu --storage-location=us`

3. Make image public so that any authenticated GCP user can use it \
`gcloud compute images add-iam-policy-binding ${IMAGE_NAME} --member='allAuthenticatedUsers' --role='roles/compute.imageUser'`

4. Create a PR to update SkyPilot Catalog's latest version of [`images.csv`](https://github.com/skypilot-org/skypilot-catalog/blob/master/catalogs/v5/gcp/images.csv): update the ImageId for tag `skypilot:custom-gpu-debian-11`.

5. Clean up: delete deprecated images and stop sky cluster \
`sky stop ${CLUSTER_NAME}`