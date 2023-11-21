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


GCP
-------------------------------

.. _gcp-service-account:

GCP Service Account
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`GCP Service Accounts
<https://cloud.google.com/iam/docs/service-account-overview>`__ are supported.

.. tip::
    A service account on your local machine can avoid the periodic
    ``google.auth.exceptions.RefreshError: Reauthentication is needed. Please
    run `gcloud auth application-default login` to reauthenticate.`` error. A
    service account is long-lived as it does not have an expiry time.

Set up a service account as follows:

1. Follow the :ref:`instructions <gcp-service-account-creation>` to create a service account with the appropriate roles/permissions.
2. In the "Service Accounts" tab in the `IAM & Admin console
   <https://console.cloud.google.com/iam-admin/iam>`__, click on the service
   account to go to its detailed page. Click on the **KEYS** tab, then click on
   **ADD KEY** to add a JSON key.  The key will be downloaded automatically.
3. Set the environment variable ``GOOGLE_APPLICATION_CREDENTIALS`` to the path of the key file, and configure the gcloud CLI tool:

   .. code-block:: console

       $ export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
       $ gcloud auth activate-service-account --key-file=$GOOGLE_APPLICATION_CREDENTIALS
       $ gcloud config set project your-project-id

   You may want to add the export statement in your profile (e.g. ``~/.bashrc``, ``~/.zshrc``) so that it is set automatically in all new terminal sessions.
