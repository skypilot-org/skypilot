.. _cloud-permissions:

Minimal Cloud Permissions Setup
=================================

.. note::

    The instructions below are completely **optional** and are not needed to get started with SkyPilot. These steps are for users who want to make SkyPilot use non-default, restricted permissions.

SkyPilot will work out of the box for users who own the root (admin) account of their clouds, if they follow the steps in :ref:`Installation section <cloud-account-setup>`.

However, in some cases, the user may want to **limit the permissions** of the account used by SkyPilot. This is especially true if the user is using a shared cloud account. The following sections describe the permissions required by SkyPilot for each cloud provider.


.. _cloud-permissions-aws:

AWS
---

.. _cloud-permissions-aws-user-creation:

User Creation
~~~~~~~~~~~~~~

AWS accounts can be attached with a policy that limits the permissions of the account. Follow these steps to create an AWS user with the minimum permissions required by SkyPilot:

1. Open the `IAM dashboard <https://us-east-1.console.aws.amazon.com/iamv2/home#/home>`_ in the AWS console and click on the **Users** tab. Then, click **Add users** and enter the user's name. Click **Next**.

.. image:: ../../images/screenshots/aws/aws-add-user.png
    :width: 80%
    :align: center
    :alt: AWS Add User

2. In the **Permissions options** section, select "Attach existing policies directly"; Click on the **Create Policy**. This opens another window to create an IAM policy.

.. image:: ../../images/screenshots/aws/aws-create-policy.png
    :width: 80%
    :align: center
    :alt: AWS Create Policy

3. Choose "JSON" tab and place the following policy into the box. Replace the ``<account-ID-without-hyphens>`` with your AWS account ID. You can find your AWS account ID by clicking on the upper right corner of the console.

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

4. Click **Next: Tags** and follow the instructions to finish creating the policy. You can give the policy a descriptive name, such as ``minimal-skypilot-policy``.
5. Go back to the previous window and click on the refresh button, and you can now search for the policy you just created.

.. image:: ../../images/screenshots/aws/aws-add-policy.png
    :width: 80%
    :align: center
    :alt: AWS Add Policy

6. [Optional] If you would like to have your users access S3 buckets: You can additionally attach S3 access, such as the "AmazonS3FullAccess" policy.

.. image:: ../../images/screenshots/aws/aws-s3-policy.png
    :width: 80%
    :align: center
    :alt: AWS Add S3 Policy

7. Click on **Next** and follow the instructions to create the user.

With the steps above you are almost ready to have the users in your organization to use SkyPilot with the minimal permissions.

**One more thing** to do is to create a single iam role ``skypilot-v1`` for all users in your organization. There are two ways to accomplish this:

1. Add additional permission for the user you created to allow SkyPilot to automatically create the iam role using the user account. You can modify the last two rules in the policy you created in step 4 with the highlighted four lines:

.. note::

    If you have created the policy, you can find the policy in the **Policies** tab in the IAM dashboard. Click on the policy ``minimal-skypilot-policy`` (or the name you set in step 4) and click on the **Edit** to edit the policy.

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

2. Alternatively, you can create the ``skypilot-v1`` iam role manually. The following section describes how to create the iam role manually.


IAM Role Creation
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note::

    If you already have an iam role called ``skypilot-v1`` in your AWS account, it is likely created by SkyPilot automatically, and you can skip this section.

1. Click the “Roles” tab in the IAM console, and click on the “Create role”

.. image:: ../../images/screenshots/aws/aws-add-role.png
    :width: 80%
    :align: center
    :alt: AWS Add Role

2. Select the following entity and common use cases and Next

.. image:: ../../images/screenshots/aws/aws-add-role-entity.png
    :width: 80%
    :align: center
    :alt: AWS Role Entity

3. Select the policy you created in step 4 in :ref:`User Creation <cloud-permissions-aws-user-creation>` (i.e. the previous step 6) and click on “Next: Tags”.
4. [Optional] If you would like to let the user access S3 buckets on the VM they created, you can additionally attach the s3 access permission to the iam role, such as the "AmazonS3FullAccess" policy.
5. Click Next, and name your role with “skypilot-v1” and Click “Create role”


.. _cloud-permissions-gcp:

GCP
---

To use SkyPilot, your GCP account needs to be granted the following IAM roles:

.. code-block:: yaml

  roles/browser
  roles/compute.admin
  roles/iam.serviceAccountAdmin
  roles/iam.serviceAccountUser
  roles/serviceusage.serviceUsageConsumer
  roles/storage.admin

Optionally, to use TPUs, add the following role:

.. code-block:: yaml

  roles/tpu.admin

You can grant those accesses via the `GCP IAM console <https://console.cloud.google.com/iam-admin/iam>`_.
