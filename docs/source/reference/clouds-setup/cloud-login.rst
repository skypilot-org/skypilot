.. _cloud-login:

Using Cloud-specific Login Options
===================================

SkyPilot is designed to minimize cloud administration and setup steps. It should work out-of-the-box for most users without any additional setup.

For users who want to use advanced cloud-specific features (e.g., use SSO; have fine-grained control over permissions; etc.), this page provides instructions to do so.

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
