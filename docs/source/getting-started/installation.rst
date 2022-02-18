.. _installation:

Installation
============

Install Sky using pip:

.. code-block:: console

   $ # Clone the sky codebase
   $ git clone ssh://git@github.com/concretevitamin/sky-experiments.git
   $ cd sky-experiments
   $ # Sky requires python >= 3.6.
   $ pip install ".[all]"

If you only want the dependencies for certain clouds, you can also use
:code:`".[aws,azure,gcp]"`.

Cloud account setup
-------------------

Sky currently supports three major cloud providers: AWS, GCP, and Azure.  To run
tasks in the clouds, configure access to at least one cloud:

**AWS**:

.. code-block:: console

   $ # Install boto
   $ pip install boto3

   $ # Configure your AWS credentials
   $ aws configure

To get the "AWS Access Key" required by the :code:`aws configure`, please refer to the `AWS manual <https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html#Using_CreateAccessKey>`_. The *Default region name [None]:* and *Default output format [None]:* are optional.

**GCP**:

.. code-block:: console

   $ pip install google-api-python-client
   $ # Install `gcloud`; see https://cloud.google.com/sdk/docs/quickstart
   $ conda install -c conda-forge google-cloud-sdk

   $ # Init.
   $ gcloud init

   $ # Run this if you don't have a credentials file.
   $ # This will generate ~/.config/gcloud/application_default_credentials.json.
   $ gcloud auth application-default login

**Azure**:

.. code-block:: console

   $ # Install the Azure CLI
   $ pip install azure-cli==2.30.0
   $ # Login azure
   $ az login
   $ # Set the subscription to use
   $ az account set -s <subscription_id>

**Verifying cloud setup**

Sky allows you to verify that cloud credentials are correctly configured using
the CLI:

.. code-block:: console

   $ # Verify cloud account setup
   $ sky check

This will produce output verifying the correct setup of each supported cloud.

.. code-block:: text

   Checking credentials to enable clouds for Sky.
      AWS: enabled
      GCP: enabled
      Azure: enabled

   Sky will use only the enabled clouds to run tasks. To change this, configure cloud credentials, and run sky check.
