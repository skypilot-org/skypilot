# SkyPilot OS Image Generation Guide

## Prerequisites
You only need to do this once.
1. Install [Packer](https://developer.hashicorp.com/packer/tutorials/aws-get-started/get-started-install-cli)
2. Download plugins used by Packer
```bash
packer init plugins.pkr.hcl
```
3. Setup cloud credentials

## Generate Images
```bash
export CLOUD=gcp
export TYPE=gpu
export IMAGE=skypilot-${CLOUD}-${TYPE}-ubuntu

packer build ${IMAGE}.pkr.hcl
```
You will see the image ID after the build is complete.

### GCP
```bash
# NEED UPDATE. Copy from the output of Packer build
export IMAGE_NAME=skypilot-gcp-cpu-ubuntu-20241011003407 

# Make image public
export IMAGE_ID=projects/sky-dev-465/global/images/${IMAGE_NAME}
gcloud compute images add-iam-policy-binding ${IMAGE_NAME} --member='allAuthenticatedUsers' --role='roles/compute.imageUser'
```

### AWS
```bash
# Make the main image public
export IMAGE_ID=ami-0b31b24524afa8e47   # Update this
aws ec2 get-image-block-public-access-state --region us-east-1
aws ec2 modify-image-attribute --image-id ${IMAGE_ID} --launch-permission "{\"Add\": [{\"Group\":\"all\"}]}"

# Generate images for all regions and output a CSV file to be uploaded to SkyPilot Catalog
TODO
```

## Test Images
Update the image ID in `sky/clouds/gcp.py` and run the test:
```
pytest tests/test_smoke.py::test_minimal --gcp
pytest tests/test_smoke.py::test_huggingface --gcp
pytest tests/test_smoke.py::test_job_queue_with_docker --gcp
pytest tests/test_smoke.py::test_cancel_gcp
```

## Ship Images
Create a PR to update [`SkyPilot Catalog`](https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs). 

TODO: add PR examples.