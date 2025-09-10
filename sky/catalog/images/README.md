# SkyPilot OS Image Generation Guide

## Prerequisites
You only need to do this once.
1. Install [Packer](https://developer.hashicorp.com/packer/tutorials/aws-get-started/get-started-install-cli)
2. Setup cloud credentials
3. `cd sky/catalog/images/`
4. Download plugins used by Packer
```bash
packer init plugins.pkr.hcl
```

## Generate Images
FYI time to packer build images:
| Cloud | Type | Approx. Time |
|-------|------|------------------------|
| AWS   | GPU  | 15 min          |
| AWS   | CPU  | 10 min          |
| GCP   | GPU  | 16 min          |
| GCP   | CPU  | 5 min          |
| Azure | GPU  | 35 min          |
| Azure | CPU  | 25 min          |

### GCP
1. Build a single global image.
```bash
export TYPE=cpu  # Update this
export IMAGE=skypilot-gcp-${TYPE}-ubuntu
packer build ${IMAGE}.pkr.hcl
```
2. Make the image public
```bash
# Make image public
export IMAGE_NAME=skypilot-gcp-gpu-ubuntu-241030  # Update this
export IMAGE_ID=projects/sky-dev-465/global/images/${IMAGE_NAME}
gcloud compute images add-iam-policy-binding ${IMAGE_NAME} --member='allAuthenticatedUsers' --role='roles/compute.imageUser'
```

### AWS
1. Generate the source image for a single region.
For `x86_64` image:
```bash
export TYPE=cpu  # Update this
export IMAGE=skypilot-aws-${TYPE}-ubuntu
packer build ${IMAGE}.pkr.hcl
```
For `arm64` image:
```bash
export TYPE=gpu  # Update this
export IMAGE=skypilot-aws-${TYPE}-ubuntu-arm64
packer build ${IMAGE}.pkr.hcl
```
2. Copy images to all regions
```bash
export TYPE=gpu  # Update this
export IMAGE_ID=ami-0989556a89639b1bb   # Update this
python aws_utils/image_gen.py --image-id ${IMAGE_ID} --processor ${TYPE}
```
Add `--arch arm64` for `arm64` image:
```bash
export TYPE=gpu  # Update this
export IMAGE_ID=ami-0989556a89639b1bb   # Update this
python aws_utils/image_gen.py --image-id ${IMAGE_ID} --processor ${TYPE} --arch arm64
```
3. Add fallback images if any region failed \
Look for "NEED_FALLBACK" in the output `images.csv` and edit. (You can use public [ubuntu images](https://cloud-images.ubuntu.com/locator/ec2/) as fallback.)

### Azure
1. Generate a client secret for packer [here](https://portal.azure.com/?feature.msaljs=true#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Credentials/appId/1d249f23-c22e-4d02-b62b-a6827bd113fe/isMSAApp~/false).
```bash
export SECRET=xxxxxx  # Update this
```
2. Build and copy images for all regions for GPU (gen 1 & 2) and CPU (gen 2 only).
```bash
packer build --var vm_generation=2 --var client_secret=${SECRET} skypilot-azure-cpu-ubuntu.pkr.hcl
packer build --var vm_generation=2 --var client_secret=${SECRET} skypilot-azure-gpu-ubuntu.pkr.hcl
packer build --var vm_generation=1 --var client_secret=${SECRET} skypilot-azure-gpu-ubuntu.pkr.hcl
packer build  --var vm_generation=2 --var client_secret=${SECRET} --var use_grid_driver=true skypilot-azure-gpu-ubuntu.pkr.hcl
```

### Kubernetes
1. Build the image
```bash
export REGION=europe  # Update this: us, europe, asia
./skypilot-k8s-image.sh -p -l -r ${REGION}
./skypilot-k8s-image.sh -p -l -g -r ${REGION}
```

## Test Images
1. Minimal GPU test: `sky launch --image ${IMAGE_ID} --gpus=L4:1 --cloud ${CLOUD}` then run `nvidia-smi` in the launched instance.
2. Update the image ID in `sky/clouds/gcp.py` and run the test:
```bash
pytest tests/smoke_tests/test_basic.py::test_minimal --gcp
pytest tests/smoke_tests/test_cluster_job.py::test_huggingface --gcp
pytest tests/smoke_tests/test_cluster_job.py::test_job_queue_with_docker --gcp
pytest tests/smoke_tests/test_cluster_job.py::test_cancel_gcp
```

## Ship Images & Cleanup
Submit a PR to update [`SkyPilot Catalog`](https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs) then clean up the old images to avoid extra iamge storage fees.

### GCP
1. Update Catalog with new images: [example PR](https://github.com/skypilot-org/skypilot-catalog/pull/86)
2. Go to [GCP console](https://console.cloud.google.com/compute/images?tab=images&project=sky-dev-465) and delete old images.

### AWS
1. Copy the old custom image rows from Catalog's existing `images.csv` to a local `images.csv` in this folder.
2. Update Catalog with new images: [example PR](https://github.com/skypilot-org/skypilot-catalog/pull/89)
3. Delete AMIs across regions by running
```bash
python aws_utils/image_delete.py --tag ${TAG}
```

### Azure
1. Update Catalog with new images: [example PR](https://github.com/skypilot-org/skypilot-catalog/pull/92)
