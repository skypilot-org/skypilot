.. _installation:

Installation
============

Install Sky using pip:

.. code-block:: console

   $ # Sky requires python >= 3.6.
   $ pip install -e ".[all]"

If you only want the dependencies for certain clouds, you can also use
:code:`".[aws,azure,gcp]"`.

Cloud account setup
-------------------

Sky currently supports three major cloud providers: AWS, GCP, and Azure.  To run
tasks in the clouds, configure access to at least one cloud:

**AWS**: Install boto with :code:`pip install boto3` and configure your AWS
credentials using :code:`aws configure`.

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
login using :code:`az login --use-device-code`. Set the subscription to use from
the command line (:code:`az account set -s <subscription_id>`).
