
.. _cloud-permissions-aws:

AWS
=====

.. _aws-sso:

Using AWS SSO
-------------

`AWS IAM Identity Center <https://aws.amazon.com/iam/identity-center/>`_ (Successor to AWS Single Sign-On, or SSO) is supported.

.. warning::
  SSO login *will not work across multiple clouds*. If you use multiple clouds, you should **LINK ME** :ref:`set up a dedicated user and credentials <dedicated-aws-user>` so that instances launched on other clouds can use AWS resources.

To use it, ensure that your machine `has AWS CLI V2 installed <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_ (by default, ``pip install skypilot[aws]`` installs V1; V2 cannot be installed via pip).

You can use the following to check version:

.. code-block:: console

    $ aws --version
    aws-cli/2.25.6 ...

Visit your SSO login portal (e.g. ``https://my-sso-portal.awsapps.com/start``), and click on **Access keys** under the corresponding account. Under "AWS IAM Identity Center credentials (Recommended)" note:

- the ``SSO start URL``
- the ``SSO Region``

Then, log into your SSO account:

.. code-block:: console

    $ aws configure sso

- **SSO session name**: should be set, but you can choose any name you want.
- **SSO start URL**: copy from the SSO login portal
- **SSO region**: copy from the SSO login portal
- **SSO registration scopes**: leave blank to use the default

Log in and approve the request in your web browser. Then back in the CLI, complete the remaining fields:

- **Default client Region**: optional
- **CLI default output format**: optional
- **Profile name**: set to ``default`` if you want to use this profile by default with SkyPilot, otherwise see :ref:`several-aws-profiles`.

If everything is set up correctly, :code:`sky check aws` should succeed!


.. _dedicated-aws-user:

Setting up a dedicated SkyPilot IAM user
----------------------------------------

You can create a dedicated IAM user for SkyPilot, if you can't or don't want to use your normal credentials, or if you want to restrict SkyPilot's permissions.

.. note::

  **SSO users** that login via awsapps.com should set up a dedicated user to ensure cross-cloud access. Otherwise, instances launched in other clouds will not be able to access AWS resources, including buckets and managed jobs.

Follow these steps to create a new AWS user:

1. Open the `IAM dashboard <https://us-east-1.console.aws.amazon.com/iamv2/home#/home>`_ in the AWS console and click on the **Users** tab. Then, click **Create User** and enter the user's name. Click **Next**.

TODO these screenshots are all outdated

.. image:: ../../images/screenshots/aws/aws-add-user.png
    :width: 80%
    :align: center
    :alt: AWS Add User

2. In the **Permissions options** section, select "Attach existing policies directly". Depending on whether you want simplified or minimal permissions, follow the instructions below:

    .. tab-set::

        .. tab-item:: Simplified permissions

            Search for the **AdministratorAccess** policy, and check the box to add it. Click **Next** to proceed.

        .. tab-item:: Minimal permissions

            Click on **Create Policy**.

            .. image:: ../../images/screenshots/aws/aws-create-policy.png
                :width: 80%
                :align: center
                :alt: AWS Create Policy

            This will open a new window to define the minimal policy.

            Choose "JSON" tab and copy the needed minimal policy rules.
            **See** :ref:`aws-minimal-policy` **for all the policy rules.**

            Click **Next: Tags** and follow the instructions to finish creating the policy. You can give the policy a descriptive name, such as ``minimal-skypilot-policy``.

            Go back to the previous window and click on the refresh button, and you can now search for the policy you just created.

            .. image:: ../../images/screenshots/aws/aws-add-policy.png
                :width: 80%
                :align: center
                :alt: AWS Add Policy

            Check the box to add the policy, and click **Next** to proceed.

3. Click on **Next** and follow the instructions to create the user.

4. Select the newly created user from the dashboard, and go to the **Security credentials** tab.

5. Click on **Create access key**, and for "Use case", select **Other**. Click **Next** , then click **Create access key**.

6. Use the newly created access key to configure your credentials with the AWS CLI:

.. code-block:: shell

  # Configure your AWS credentials
  aws configure

  # Validate that credentials are working
  sky check aws



.. _several-aws-profiles:

Using several profiles or accounts
----------------------------------

You can use different AWS profiles or accounts to launch different clusters. SkyPilot will remember the owner identity of each cluster and properly protects any "write" operations. All clusters are shown in ``sky status``.

Example of mixing a default profile and an SSO profile:

.. code-block:: console

    $ # A cluster launched under the default AWS identity.
    $ sky launch --cloud aws -c default

    $ # A cluster launched under a different profile.
    $ AWS_PROFILE=AdministratorAccess-12345 sky launch --cloud aws -c my-sso-cluster


.. _cloud-permissions-aws-user-creation:

Minimal permissions
-----------------------

If you want to minimize the AWS permissions used by SkyPilot, you should set up the minimal permissions in two places:

1. :ref:`User Account <aws-create-minimal-user>`: the user account is the individual account of an user created by the administrator.
2. :ref:`IAM role <iam-role-creation>`: the IAM role is assigned to all EC2 instances created by SkyPilot, which is used by the instances to access AWS resources, e.g., read/write S3 buckets or create other EC2 nodes. The IAM role is shared by all users under the same organization/root account. (If a user account has the permission to create IAM roles, SkyPilot can automatically create the role.)

.. _aws-create-minimal-user:

Create a user account with minimal permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Follow the instructions above for :ref:`dedicated-aws-user`. When setting permissions for the user, use the :ref:`aws-minimal-policy` rules below.

.. _iam-role-creation:

Create an IAM role with minimal permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note::
    In most cases, the IAM role will be automatically created. You only need to manually create the IAM role if you have exlucded the optional role creation permissions from your minimal skypilot policy.

    If you already have an IAM role called ``skypilot-v1`` in your AWS account, it is likely created by SkyPilot automatically, and you can skip this section.

1. Click the "Roles" tab in the IAM console, and click on **Create role**.

.. image:: ../../images/screenshots/aws/aws-add-role.png
    :width: 80%
    :align: center
    :alt: AWS Add Role

2. Select the following entity and common use cases and click **Next**.

.. image:: ../../images/screenshots/aws/aws-add-role-entity.png
    :width: 80%
    :align: center
    :alt: AWS Role Entity

3. Select the policy you created in :ref:`User Creation <dedicated-aws-user>` click on **Next: Tags**.
4. Click **Next**, and name your role "skypilot-v1". Click **Create role**.


.. _aws-minimal-policy:

Minimal IAM policy rules
~~~~~~~~~~~~~~~~~~~~~~~~

To avoid giving SkyPilot administrator access, you can create a policy that limits the permissions of the account. These are the minimal policy rules required by SkyPilot:

.. note::
    **Replace the** ``<account-ID-without-hyphens>`` **with your AWS account ID**. You can find your AWS account ID by clicking on the upper right corner of the console.

.. code-block:: json
    :name: aws-policy-json

    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": "arn:aws:ec2:*::image/ami-*"
            },
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": [
                    "arn:aws:ec2:*:<account-ID-without-hyphens>:instance/*",
                    "arn:aws:ec2:*:<account-ID-without-hyphens>:network-interface/*",
                    "arn:aws:ec2:*:<account-ID-without-hyphens>:subnet/*",
                    "arn:aws:ec2:*:<account-ID-without-hyphens>:volume/*",
                    "arn:aws:ec2:*:<account-ID-without-hyphens>:security-group/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:DeleteTags",
                    "ec2:StartInstances",
                    "ec2:CreateTags",
                    "ec2:StopInstances"
                ],
                "Resource": "arn:aws:ec2:*:<account-ID-without-hyphens>:instance/*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:Describe*"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateSecurityGroup",
                    "ec2:AuthorizeSecurityGroupIngress"
                ],
                "Resource": "arn:aws:ec2:*:<account-ID-without-hyphens>:*"
            },
            {
                "Effect": "Allow",
                "Action": "iam:CreateServiceLinkedRole",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "iam:AWSServiceName": "spot.amazonaws.com"
                    }
                }
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:PassRole"
                ],
                "Resource": [
                    "arn:aws:iam::<account-ID-without-hyphens>:role/skypilot-v1"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetInstanceProfile"
                ],
                "Resource": "arn:aws:iam::<account-ID-without-hyphens>:instance-profile/skypilot-v1"
            }
        ]
    }

**Optional**: If you would like SkyPilot to automatically set up an IAM role and instance profile for EC2 instances, modify the last two rules in the policy with the highlighted four lines:

.. warning::

    If you don't do this, you must manually set up the IAM role that SkyPilot will use: see :ref:`iam-role-creation`.

.. code-block:: json
    :emphasize-lines: 6-7,17-18

            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:PassRole",
                    "iam:CreateRole",
                    "iam:AttachRolePolicy"
                ],
                "Resource": [
                    "arn:aws:iam::<account-ID-without-hyphens>:role/skypilot-v1"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetInstanceProfile",
                    "iam:CreateInstanceProfile",
                    "iam:AddRoleToInstanceProfile"
                ],
                "Resource": "arn:aws:iam::<account-ID-without-hyphens>:instance-profile/skypilot-v1"
            }

**Optional**: To enable ``sky launch --clone-disk-from``, you need to add the following permissions to the policy above as well.

.. code-block:: json

           {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateImage",
                    "ec2:CopyImage",
                    "ec2:DeregisterImage"
                ],
                "Resource": "*"
            }

**Optional**: To enable opening ports on AWS cluster, you need to add the following permissions to the policy above as well.

.. code-block:: json

           {
                "Effect": "Allow",
                "Action": [
                    "ec2:DeleteSecurityGroup",
                    "ec2:ModifyInstanceAttribute"
                ],
                "Resource": "arn:aws:ec2:*:<account-ID-without-hyphens>:*"
            }


**Optional**: If you would like to have your users access S3 buckets, you need to add the following permissions to the policy above as well.

.. code-block:: json

           {
                "Effect": "Allow",
                "Action": [
                    "s3:*",
                ],
                "Resource": "*"
            }


Using a specific VPC
-----------------------
By default, SkyPilot uses the "default" VPC in each region. If a region does not have a `default VPC <https://docs.aws.amazon.com/vpc/latest/userguide/work-with-default-vpc.html#create-default-vpc>`_, SkyPilot will not be able to use the region.

To instruct SkyPilot to use a specific VPC, you can use SkyPilot's global config
file ``~/.sky/config.yaml`` to specify the VPC name in the ``aws.vpc_name``
field:

.. code-block:: yaml

    aws:
      vpc_name: my-vpc-name

See details in :ref:`config-yaml`.  Example use cases include using a private VPC or a
VPC with fine-grained constraints, typically created via Terraform or manually.

To manually create a private VPC (i.e., all nodes will have internal IPs only),
you can use the AWS console; see instructions `here
<https://github.com/skypilot-org/skypilot/pull/1512>`_.
