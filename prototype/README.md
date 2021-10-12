# Sky Prototype

## Setup

0. `pip install -e .`

1. Clone the the [ResNet repo (gpu_train branch)](https://github.com/concretevitamin/tpu/tree/gpu_train/models/official/resnet) to your local machine somewhere.  Set the directory in `workdir` inside `resnet_app.py`.

2. `python resnet_app.py`, or `python -u main.py`

## Cloud account setup
Running these setup enables Sky to launch resources on different clouds.
This should be run on your laptop/development machine where you will use Sky to launch jobs.

TODO: see https://github.com/banzaicloud/cloudinfo#cloud-credentials for a reference

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
