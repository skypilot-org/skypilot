# SkyPilot OS Image Generation Guide

## Prerequisites
You only need to do this once.
1. Install [Packer](https://developer.hashicorp.com/packer/tutorials/aws-get-started/get-started-install-cli)
2. Download plugins used by Packer
```bash
packer init plugins.pkr.hcl
```

## Generate Images
```bash
CLOUD=gcp
TYPE=gpu
OS=ubuntu
IMAGE=skypilot-${CLOUD}-${TYPE}-${OS}

packer build ${IMAGE}.pkr.hcl
```
You will see the image ID after the build is complete.

### GCP
```bash
# NEED UPDATE. Copy from the output of Packer build
IMAGE_NAME=skypilot-gcp-cpu-ubuntu-20241011003407 

# Make image public
IMAGE_ID=projects/sky-dev-465/global/images/${IMAGE_NAME}
gcloud compute images add-iam-policy-binding ${IMAGE_NAME} --member='allAuthenticatedUsers' --role='roles/compute.imageUser'
```

### AWS
```bash
# NEED UPDATE. Copy from the output of Packer build
IMAGE_ID=ami-0981cc842c7188227

# TODO: Generate images for all regions and output a CSV file to be uploaded to SkyPilot Catalog
```

## Test GPU Images
### Option 1: Minimal Test
```bash
sky launch --cloud=${CLOUD} --image-id=${IMAGE_ID} --gpus=L4:1
```
ssh into the instance and check if NVIDIA driver is correctly set up:
```bash
nvidia-smi
```

### Option 2: ML Training Test
Go to `examples/huggingface_glue_imdb_app.yaml`, add following to the resource section:
```
cloud: ${CLOUD}
image_id: ${IMAGE_ID}
region: ${REGION}       # AWS only
```
Then run the test: 
```bash
pytest tests/test_smoke.py::test_huggingface
```

## Ship Images
Create a PR to update [`SkyPilot Catalog`](https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs). For example,
* GCP: TODO(link PR)
* AWS: TODO(link PR)
* Azure: TODO(link PR)