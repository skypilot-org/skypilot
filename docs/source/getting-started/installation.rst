.. _installation:

Installation
============

Install Sky using pip:

.. code-block:: console

  $ # Sky requires python >= 3.6 and < 3.10.
  $ git clone ssh://git@github.com/sky-proj/sky.git
  $ cd sky
  $ pip install ".[all]"

Sky currently supports three major cloud providers: AWS, GCP, and Azure.  If you
only have access to certain clouds, use any combination of
:code:`".[aws,azure,gcp]"` (e.g., :code:`".[aws,gcp]"`) to reduce the
dependencies installed.

Cloud account setup
-------------------

To run tasks in the clouds, configure access to at least one cloud:

**AWS**

.. code-block:: console

  $ # Install boto
  $ pip install boto3

  $ # Configure your AWS credentials
  $ aws configure

To get the **AWS Access Key** required by :code:`aws configure`, please refer to the `AWS manual <https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html#Using_CreateAccessKey>`_. The **Default region name [None]:** and **Default output format [None]:** fields are optional.

**GCP**

.. code-block:: console

  $ pip install google-api-python-client
  $ conda install -c conda-forge google-cloud-sdk

  $ gcloud init

  $ # Run this if you don't have a credentials file.
  $ # This will generate ~/.config/gcloud/application_default_credentials.json.
  $ gcloud auth application-default login

If you meet the following error (*RemoveError: 'requests' is a dependency of conda and cannot be removed from conda's operating environment*) while running :code:`conda install -c conda-forge google-cloud-sdk`, please try :code:`conda update --force conda` and run it again.


**Azure**

.. code-block:: console

  $ # Install the Azure CLI
  $ pip install azure-cli==2.30.0
  $ # Login
  $ az login
  $ # Set the subscription to use
  $ az account set -s <subscription_id>

**Verifying cloud setup**

Optionally, you can run :code:`sky check` to verify cloud credentials are correctly configured:

.. code-block:: console

  $ # Verify cloud account setup
  $ sky check

This will produce outputs verifying the correct setup of each supported cloud.

.. code-block:: text

  Checking credentials to enable clouds for Sky.
    AWS: enabled
    GCP: enabled
    Azure: enabled

  Sky will use only the enabled clouds to run tasks. To change this, configure cloud credentials, and run sky check.
