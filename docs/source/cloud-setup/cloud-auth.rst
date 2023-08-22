.. _cloud-auth:

Cloud Authentication
===================================


.. tip::

   More topics will be added to this page gradually as they surface.

   Contributions are especially welcome on administration as cloud accounts differ case-by-case.


AWS
-------------------------------

.. _aws-sso:

AWS SSO
~~~~~~~~~~~
`AWS IAM Identity Center <https://aws.amazon.com/iam/identity-center/>`_ (Successor to AWS Single Sign-On, or SSO) is supported.

To use it, ensure that your machine `has AWS CLI V2 installed <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_ (by default, ``pip install skypilot[aws]`` installs V1; V2 cannot be installed via pip).

You can use the following to check version:

.. code-block:: console

    $ aws --version


Then, after the usual ``aws configure sso`` and ``aws sso login --profile <profile_name>`` commands, SkyPilot will work as usual.

Using several profiles or accounts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can use different AWS profiles or accounts to launch different clusters. SkyPilot will remember the owner identity of each cluster and properly protects any "write" operations. All clusters are shown in ``sky status``.

Example of mixing a default profile and an SSO profile:

.. code-block:: console

    $ # A cluster launched under the default AWS identity.
    $ sky launch --cloud aws -c default

    $ # A cluster launched under a different profile.
    $ AWS_PROFILE=AdministratorAccess-12345 sky launch --cloud aws -c my-sso-cluster

Using temporary credentials
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can leverage aws-vault and docker to use temporary credentials. First, install and setup `aws-vault <https://github.com/99designs/aws-vault>`__ . 

Once done, add your aws-profile:

.. code-block:: console
    
    $ aws-vault add skypilot


Then use the two following snippets:

*docker-compose.yml*

.. code-block:: yaml

    version: '3.3'
    services:
    skypilot:
        image: berkeleyskypilot/skypilot:latest
        container_name: skypilot
        environment:
        AWS_VAULT: ${AWS_VAULT}
        AWS_REGION: ${AWS_REGION}
        AWS_DEFAULT_REGION: ${AWS_DEFAULT_REGION}
        AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID}
        AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY}
        AWS_SESSION_TOKEN: ${AWS_SESSION_TOKEN}
        AWS_CREDENTIAL_EXPIRATION: ${AWS_CREDENTIAL_EXPIRATION}
        volumes:
            - ./credentials_script.sh:/root/credentials_script.sh
        command: sh -c "chmod +x /root/credentials_script.sh && sh /root/credentials_script.sh && tail -f /dev/null" 

*credentials_script.sh*

.. code-block:: bash

    #!/bin/bash
    output_file="/root/.aws/credentials"

    mkdir -p "$(dirname "$output_file")"
    cat << EOF > "$output_file"
    [default]
    aws_access_key_id = $AWS_ACCESS_KEY_ID
    aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
    aws_region = $AWS_REGION
    EOF


Build and up your docker-compose:

.. code-block:: console

    $ docker-compose build
    $ aws-vault exec skypilot --duration 1h -- docker-compose up -d
    $ # --duration 1h means your credentials will expire in one hour

Finally run:

.. code-block:: console

    $ docker exec -it skypilot bash

Here you can type any Skypilot command with temporary credentials.

GCP
-------------------------------

.. _gcp-service-account:

GCP Service Account
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`GCP Service Account <https://cloud.google.com/iam/docs/service-account-overview>`__ is supported.

To use it to access GCP with SkyPilot, you need to setup the credentials:

1. Download the key for the service account from the `GCP console <https://console.cloud.google.com/iam-admin/serviceaccounts>`__.
2. Set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to the path of the key file, and configure the gcloud CLI tool:

.. code-block:: console

    $ export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
    $ gcloud auth activate-service-account --key-file=$GOOGLE_APPLICATION_CREDENTIALS
    $ gcloud config set project your-project-id
