# Sky Prototype

## Setup

```bash
pip install -e .

# Clone the the ResNet repo (gpu_train branch)
#   https://github.com/concretevitamin/tpu/tree/gpu_train/models/official/resnet
# to your local machine's ~/Downloads/.

python resnet_app.py
```

## Cloud account setup
Running these setup enables Sky to launch resources on different clouds.
This should be run on your laptop/development machine where you will use Sky to launch jobs.

TODO: see https://github.com/banzaicloud/cloudinfo#cloud-credentials for a reference.

**AWS**. TODO.

**GCP**. Run:
```
pip install google-api-python-client
# Install `gcloud`; see https://cloud.google.com/sdk/docs/quickstart

# Init.
gcloud init

# Run this if you don't have a credentials file.
# This will generate ~/.config/gcloud/application_default_credentials.json.
gcloud auth application-default login
```
TODO: allow user to set up her own project_id and pass in somewhere.

*Quotas.* Increase your GPU quotas according to
* [Checking GPU quota](https://cloud.google.com/compute/docs/gpus/create-vm-with-gpus#check-quota)
* [GPU regions and zones availability](https://cloud.google.com/compute/docs/gpus/gpu-regions-zones)

*Handy commands.*
```
# Check V100's usage/limit.
gcloud compute regions describe us-west1 | grep V100 -A2

# SSH in.
ray attach config/gcp.yml

# Teardown the resources.
ray down config/gcp.yml
```

**Azure**. TODO.

## Open issues

Resource provisioning
* If a zone runs out of GPUs, may need to try different zones.

## Design notes

*Empower the user to debug things.*  This means exposing SSH access to the launched VMs, saving and priting logs, etc.
