.. _cloud-permissions:

Cloud Permissions Setup
=======================

SkyPilot will work out of the box for users who own the root (admin) account of their clouds, if they follow the steps in :ref:`Installation section <cloud-account-setup>`.

However, in some cases, the user may want to **limit the permissions** of the account used by SkyPilot. This is especially true if the user is using a shared cloud account. The following sections describe the permissions required by SkyPilot for each cloud provider.

.. _cloud-permissions-aws:

AWS
---

.. _cloud-permissions-aws-user-creation:

User Creation
~~~~~~~~~~~~~

AWS accounts can be attached with a policy that limits the permissions of the account. Follow these steps to create an AWS user with the minimum permissions required by SkyPilot:

1. Open the IAM dashboard in the AWS console and click on the "Users" tab. Then, click "Add users" and enter the user's name.

.. image:: ../images/screenshots/aws/aws-add-user.png
    :width: 80%
    :align: center
    :alt: AWS Add User
3. In the "Add user to group" section, select "Attach existing policies directly"; Click on the "Create Policy" button. This opens another window to create an IAM policy.

.. image:: ../images/screenshots/aws/aws-create-policy.png
    :width: 80%
    :align: center
    :alt: AWS Create Policy
4. Choose "JSON" tab and place the following policy into the box. Replace the ``<account-ID-without-hyphens>`` with your AWS account ID. You can find your AWS account ID by clicking on the upper right corner of the console.

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
5. Click “Next: Tags” and follow the instructions to create the policy.
6. Go back to the previous window and click on the refresh button, and you can now search for the policy you just created.

.. image:: ../images/screenshots/aws/aws-add-policy.png
    :width: 80%
    :align: center
    :alt: AWS Add Policy
7. [Optional] If you would like to have your users access the s3 bucket. You can additionally attach the S3 access, such as the "AmazonS3FullAccess" policy.

.. image:: ../images/screenshots/aws/aws-s3-policy.png
    :width: 80%
    :align: center
    :alt: AWS Add S3 Policy

8. Click on "Next" and follow the instructions to create the user.

With the steps above you are almost ready to have the users in your organization to use SkyPilot with the minimal permissions.

**One more thing** to do is to create a single service account "skypilot-v1" for all the users in your organization. There are two ways to accomplish this:

1. Add additional permission for the user you created to allow SkyPilot to automatically create the service account using the user account. You can modify the last two rules in the policy you created in step 4 with the highlighted three lines:

.. code-block:: json
    :emphasize-lines: 6-7,17

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
2. Instead, you can create the "skypilot-v1" service account manually. The following section describe how to create the service account manually.


Service Account Creation
~~~~~~~~~~~~~~~~~~~~~~~~
1. Click the “Roles” tab in the IAM console, and click on the “Create role”

.. image:: ../images/screenshots/aws/aws-add-role.png
    :width: 80%
    :align: center
    :alt: AWS Add Role

2. Select the following entity and common use cases and Next

.. image:: ../images/screenshots/aws/aws-add-role-entity.png
    :width: 80%
    :align: center
    :alt: AWS Role Entity

3. Select the policy you created in step 4 in :ref:`User Creation <cloud-permissions-aws-user-creation>` (i.e. the previous step 6) and click on “Next: Tags”.
4. [Optional] If you would like to let the user access S3 buckets on the VM they created, you can additionally attach the s3 access permission to the service account, such as the "AmazonS3FullAccess" policy.
5. Click Next, and name your role with “skypilot-v1” and Click “Create role”
