.. _installation:

Installation
============

Install Sky using pip:

.. code-block:: console

   $ pip install -e ".[all]"

If you only want the dependencies for certain clouds, you can also use
:code:`".[aws,azure,gcp]"`.

Cloud account setup
-------------------

**AWS**: Install boto with :code:`pip install boto3` and configure your AWS
credentials in :code:`~/.aws/credentials` using :code:`aws configure`.

**GCP**: Run the following:

.. code-block::

   pip install google-api-python-client
   # Install `gcloud`; see https://cloud.google.com/sdk/docs/quickstart
   conda install -c conda-forge google-cloud-sdk

   # Init.
   gcloud init

   # Run this if you don't have a credentials file.
   # This will generate ~/.config/gcloud/application_default_credentials.json.
   gcloud auth application-default login

**Azure**: Install the Azure CLI with :code:`pip install azure-cli` and then
login using :code:`az login --use-device-code`. Set the subscription to use from the command line
(:code:`az account set -s <subscription_id>`) or by modifying the provider
section of the Azure template (:code:`config/azure.yml.j2`). *NOTE*: Ray
Autoscaler does not work with the latest version of :code:`azure-cli`. Hotfix:
:code:`pip install azure-cli-core==2.22.0` (this will make Ray work but at the
cost of making the :code:`az` CLI tool unusable).