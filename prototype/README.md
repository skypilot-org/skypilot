# Sky Prototype

![pytest](https://github.com/concretevitamin/sky-experiments/actions/workflows/pytest.yml/badge.svg)

## Setup

```bash
# Sky requires python version >= 3.6

pip install -r requirements.txt
pip install -e .

python examples/resnet_app.py

# Or try other examples:
ls examples/
```

## Cloud account setup
Running these setup enables Sky to launch resources on different clouds.
This should be run on your laptop/development machine where you will use Sky to launch jobs.

TODO: see https://github.com/banzaicloud/cloudinfo#cloud-credentials for a reference.

**AWS**. Install boto (`pip install boto3`) and configure your AWS credentials in `~/.aws/credentials`, as described in the [boto docs](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html).

**GCP**. Run:
```
pip install google-api-python-client
# Install `gcloud`; see https://cloud.google.com/sdk/docs/quickstart
conda install -c conda-forge google-cloud-sdk

# Init.
gcloud init

# Run this if you don't have a credentials file.
# This will generate ~/.config/gcloud/application_default_credentials.json.
gcloud auth application-default login
```
TODO: allow user to set up/create her own project_id and pass in somewhere.

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

**Azure**. Install the Azure CLI (`pip install azure-cli`) then login using `az login`. Set the subscription to use from the command line (`az account set -s <subscription_id>`) or by modifying the provider section of the Azure template (`config/azure.yml.j2`). Ray Autoscaler does not work with the latest version of `azure-cli`. Hotfix: `pip install azure-cli-core==2.22.0` (this will make Ray work but at the cost of making the `az` CLI tool unusable).

## Open issues

Resource provisioning
* If a zone runs out of GPUs, may need to try different zones.

## Design notes

*Empower the user to debug things.*  This means exposing SSH access to the launched VMs, saving and priting logs, etc.
