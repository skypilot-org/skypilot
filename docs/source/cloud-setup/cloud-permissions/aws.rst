AWS
=====

.. note::

    By default, SkyPilot will use the credentials you have set up locally. For most cases, the :ref:`installation instructions <aws-installation>` are all you need to do. The steps below are **optional advanced configuration options**, aimed primarily at cloud admins and advanced users.


.. _aws-sso:

Using AWS SSO
-------------

`AWS IAM Identity Center <https://aws.amazon.com/iam/identity-center/>`_ (successor to AWS Single Sign-On, or SSO) is supported.

.. note::

    If you use AWS SSO and multiple clouds, check the :ref:`SSO multi-cloud compatibility notes <sso-feature-compat>`.


To use SSO, ensure that your machine `has AWS CLI v2 installed <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_. By default, ``pip install skypilot[aws]`` installs v1; v2 cannot be installed via pip. To use your newly installed AWS v2 CLI, use the absolute path to the CLI (by default, ``/usr/local/aws-cli/aws``) or create an alias ``alias awsv2=/usr/local/aws-cli/aws``.

You can use the following to check version:

.. code-block:: console

    $ aws --version
    aws-cli/2.25.6 ...

Visit your SSO login portal (e.g. ``https://my-sso-portal.awsapps.com/start``), and click on **Access keys** under the corresponding account. Under "AWS IAM Identity Center credentials (Recommended)", copy these values:

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


.. _aws-ssm:

Using AWS Systems Manager (SSM)
--------------------------------

`AWS Systems Manager Session Manager <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html>`_ provides secure shell access to EC2 instances without requiring direct network access through SSH ports or bastion hosts.

SkyPilot can be configured to use SSM for SSH connections by setting ``use_ssm`` to true in your :ref:`config.yaml <config-yaml>` file under the ``aws`` section. One use case for SSM is to enable running SkyPilot for AWS instances that do not have public IPs. By also setting ``use_internal_ips`` to true in your :ref:`config.yaml <config-yaml>` file under the ``aws`` section SkyPilot will use private IPs to connect to the instances and will have access via SSM.

Prerequisites
~~~~~~~~~~~~~

Before using SSM with SkyPilot, ensure the following:

1. **AWS CLI v2** and **Session Manager plugin** are installed. Follow the `installation guide <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>`_ for your operating system.

   .. note::
      If using a :ref:`remote API server <sky-api-server>`, install the Session Manager plugin on the API server instead of your local machine.

2. **IAM permissions** are configured to allow SSM access:

   - Your user/role needs ``ssm:StartSession`` permission
   - The ``skypilot-v1`` IAM role (used by EC2 instances) needs the ``AmazonSSMManagedInstanceCore`` managed policy attached

   To attach the SSM policy to the SkyPilot role:

   a. Open the `IAM console <https://console.aws.amazon.com/iam/>`_ and go to **Roles**
   b. Search for and select the ``skypilot-v1`` role
   c. Click **Add permissions** → **Attach policies**
   d. Search for ``AmazonSSMManagedInstanceCore`` and select it
   e. Click **Add permissions**

Configuration
~~~~~~~~~~~~~

Add the following to your ``~/.sky/config.yaml`` file:

.. code-block:: yaml

    aws:
        use_ssm: true

Once configured, SkyPilot will automatically use SSM for all SSH connections to your AWS instances in the specified regions.


.. _sso-feature-compat:

Multi-cloud access with SSO login
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SSO login has limited functionality *across multiple clouds*. If you use multiple clouds, you can :ref:`set up a dedicated IAM user and access key <dedicated-aws-user>` so that instances launched on other clouds can use AWS resources.

.. tip::

    If you are running SkyPilot on an EKS cluster and need S3 access without static credentials, see :ref:`aws-eks-iam-roles` for setting up IAM roles for EKS pods.

.. list-table::
   :header-rows: 1

   * - *Supported features:*
     - SSO credentials
     - Static credentials
   * - Use S3 buckets on an AWS cluster
     - |:white_check_mark:|
     - |:white_check_mark:|
   * - Use S3 buckets on a cluster in another cloud
     - |:x:|
     - |:white_check_mark:|
   * - Run :ref:`managed jobs <managed-jobs>` across multiple clouds
     - |:yellow_circle:| [1]_
     - |:white_check_mark:|

.. [1] To allow managed jobs to run on AWS instances, make sure your controller is also on AWS, by :ref:`specifying the controller resources <jobs-controller-custom-resources>`.


.. _several-aws-profiles:

Switching profiles or accounts
------------------------------

You can use different AWS profiles or accounts to launch different clusters. SkyPilot will remember the owner identity of each cluster and properly protects any "write" operations. All clusters are shown in ``sky status``.

Example of mixing the default profile and another profile:

.. code-block:: console

    $ # A cluster launched under the default AWS identity.
    $ sky launch --infra aws -c default

    $ # A cluster launched under a different profile.
    $ AWS_PROFILE=AdministratorAccess-12345 sky launch --infra aws -c other-profile-cluster

If you are using a :ref:`remote API server <sky-api-server>`, the AWS credentials are configured on the remote server. Overriding ``AWS_PROFILE`` on the client side won't work.


Using a specific VPC
-----------------------
By default, SkyPilot uses the "default" VPC in each region. If a region does not have a `default VPC <https://docs.aws.amazon.com/vpc/latest/userguide/work-with-default-vpc.html#create-default-vpc>`_, SkyPilot will not be able to use the region.

To instruct SkyPilot to use a specific VPC, you can use SkyPilot's global config
file ``~/.sky/config.yaml`` to specify the VPC name in the ``aws.vpc_names``
field:

.. code-block:: yaml

    aws:
      vpc_names: my-vpc-name

See details in :ref:`config-yaml`.  Example use cases include using a private VPC or a
VPC with fine-grained constraints, typically created via Terraform or manually.

To manually create a private VPC (i.e., all nodes will have internal IPs only),
you can use the AWS console; see instructions `here
<https://github.com/skypilot-org/skypilot/pull/1512>`_.


..
    These two aren't currently used, but keep them so that old links like
    /aws.html#cloud-permissions-aws will still jump to here.
.. _cloud-permissions-aws:
.. _cloud-permissions-aws-user-creation:

.. _dedicated-aws-user:

Dedicated SkyPilot IAM user
---------------------------

You can optionally create a dedicated IAM user for SkyPilot with specifically granted permissions. **Creating a dedicated user is not required** --- as long as you have AWS CLI credentials set up, SkyPilot will automatically use those credentials.

However, using a dedicated IAM user can have some benefits:

- Avoid using your personal credentials with SkyPilot.
- Specify minimal permissions to avoid granting broad access to SkyPilot.
- If you use SSO login, enable some :ref:`additional cross-cloud features <sso-feature-compat>`.

Follow these steps to create a new AWS user:

1. Open the `IAM dashboard <https://us-east-1.console.aws.amazon.com/iamv2/home#/home>`_ in the AWS console and click on the **Users** tab.

   .. image:: ../../images/screenshots/aws/aws-add-user.png
       :alt: AWS Add User


   Then, click **Create User** and enter the user's name. Click **Next**.

2. In the **Permissions options** section, select "Attach existing policies directly". Depending on whether you want simplified or minimal permissions, follow the instructions below:

   .. tab-set::

       .. tab-item:: Simplified permissions

           Search for the **AdministratorAccess** policy, and check the box to add it. Click **Next** to proceed.

           .. tip::

            To use AWS for S3 but not for launching VMs, add **AmazonS3FullAccess** policy instead.

       .. tab-item:: Minimal permissions

           Click on **Create Policy**.

           .. image:: ../../images/screenshots/aws/aws-create-policy.png
               :alt: AWS Create Policy

           This will open a new window to define the minimal policy. Follow the instructions to create a new policy: :ref:`aws-minimal-policy`.

           Come back to this window, and in the **Permissions policies** box, click on the refresh button. You can now search for the policy you just created.

           .. image:: ../../images/screenshots/aws/aws-add-policy.png
               :alt: AWS Add Policy

           Check the box to add the policy, and click **Next** to proceed.

3. Click on **Next** and follow the instructions to create the user.

4. Select the newly created user from the dashboard, and go to the **Security credentials** tab. Click on **Create access key**.

   .. image:: ../../images/screenshots/aws/aws-create-access-key.png
       :alt: AWS Create access key

5. For "Use case", select **Other**. Click **Next**, then click **Create access key**.

6. Use the newly created access key to configure your credentials with the AWS CLI:

   .. code-block:: console
     :emphasize-lines: 13-14

     $ # Configure your AWS credentials
     $ aws configure
     AWS Access Key ID [None]: <Access key>
     AWS Secret Access Key [None]: <Secret access key>
     Default region name [None]:
     Default output format [None]:

     $ # Check that AWS sees the shared-credentials-files
     $ aws configure list
           Name                    Value             Type    Location
           ----                    -----             ----    --------
        profile                <not set>             None    None
     access_key     ****************xxxx shared-credentials-file
     secret_key     ****************xxxx shared-credentials-file
         region                <not set>             None    None

     $ # Validate that credentials are working
     $ sky check aws -v


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

Create the internal IAM role for SkyPilot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note::
    In most cases, the IAM role will be automatically created. You only need to manually create the IAM role if you have excluded the optional role creation permissions from your minimal skypilot policy.

    If you already have an IAM role called ``skypilot-v1`` in your AWS account, it is likely created by SkyPilot automatically, and you can skip this section.

1. If you haven't yet, :ref:`create a minimal IAM policy for SkyPilot <aws-minimal-policy>`. If you previously created a dedicated IAM user with minimal permissions, you can re-use the same policy you used for the dedicated user.

2. In the `IAM dashboard <https://us-east-1.console.aws.amazon.com/iamv2/home#/home>`_, go to the "Roles" tab, and click on **Create role**.

   .. image:: ../../images/screenshots/aws/aws-add-role.png
       :alt: AWS Add Role

3. Select **Trusted entity type**: AWS service, and **Use case**: EC2, as seen in the image below.

   .. image:: ../../images/screenshots/aws/aws-add-role-entity.png
       :alt: AWS Role Entity, with "Trusted entity type" set to "AWS service", "Service or use case" set to "EC2", and "Use case" set to "EC2".

   Click **Next**.

4. Search for and select the IAM policy from step 1.
5. Click **Next**, and name your role exactly ``skypilot-v1``. Click **Create role**.


.. _aws-minimal-policy:

Minimal IAM policy
~~~~~~~~~~~~~~~~~~

To avoid giving SkyPilot administrator access, you can create a policy that limits the permissions of the account.

When creating the policy, use the **JSON** policy editor. You can copy in the minimal policy rules and additional optional policy rules.

These are the minimal policy rules required by SkyPilot:

.. note::
    **Replace the** ``<account-ID-without-hyphens>`` **with your AWS account ID**. You can find your AWS account ID by clicking on the upper right corner of the console.

.. note::
    There are **additional optional rules** below. It's likely that you will want to use some of these, so please take a look.

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
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject"
                ],
                "Resource": "arn:aws:s3:::*/*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:ListBucket",
                    "s3:GetBucketLocation"
                ],
                "Resource": "arn:aws:s3:::*"
            },
            {
                "Effect": "Allow",
                "Action": "s3:ListAllMyBuckets",
                "Resource": "*"
            }

**Optional**: If you also want to allow SkyPilot to create and delete S3 buckets (for ``sky storage`` commands), add these additional permissions:

.. code-block:: json

           {
                "Effect": "Allow",
                "Action": [
                    "s3:CreateBucket",
                    "s3:DeleteBucket",
                    "s3:PutBucketTagging"
                ],
                "Resource": "arn:aws:s3:::*"
            }

.. tip::

    If you are using EKS and want to set up S3 access with IAM roles, see :ref:`aws-eks-iam-roles`.

**Once you have added all needed policies, click Next** and follow the instructions to finish creating the policy. You can give the policy a descriptive name, such as ``minimal-skypilot-policy``.


.. _aws-troubleshooting:

Troubleshooting
---------------

If your credentials are not being picked up, or you're seeing the wrong credentials in SkyPilot, here are some steps you can take to troubleshoot:

1. **Check** ``aws configure list``. This command should show the currently configured credentials.

   If you have static credentials set up correctly, you should see something like this:

   .. code-block:: console

       $ aws configure list
             Name                    Value             Type    Location
             ----                    -----             ----    --------
          profile                <not set>             None    None
       access_key     ****************xxxx shared-credentials-file
       secret_key     ****************xxxx shared-credentials-file
           region                <not set>             None    None

   If you have SSO credentials set up correctly, you should see something like this:

   .. code-block:: console

       $ aws configure list
             Name                    Value             Type    Location
             ----                    -----             ----    --------
          profile                <not set>             None    None
       access_key     ****************xxxx              sso
       secret_key     ****************xxxx              sso
           region                <not set>             None    None

2. **Check** ``sky check aws``. This should show whether SkyPilot is picking up the credentials and has the necessary permissions.

   .. code-block:: console

       $ sky check aws -v
       Checking credentials to enable clouds for SkyPilot.
         AWS: enabled [compute, storage]
           Activated account: VRSC9IFFYQI7THCKR5UVC [account=190763068689]
       ...

Common issues
~~~~~~~~~~~~~

- **Wrong profile is enabled.** SkyPilot will respect the ``AWS_PROFILE`` environment variable if it is set; see :ref:`several-aws-profiles`. If ``AWS_PROFILE`` is not set, SkyPilot will use the profile named ``default``.

  You may have previously set ``AWS_PROFILE`` in your ``.bashrc`` file or similar. Try to double-check the value:

  .. code-block:: console
      :emphasize-lines: 13

      $ # Check the account being used by skypilot
      $ sky check aws -v
      Checking credentials to enable clouds for SkyPilot.
        AWS: enabled [compute, storage]
          Activated account: XXXXXXXXXXXXXXXXXXXXX:user [account=123456789012]
        ...
      $ # AWS account 1234-5678-9012 is enabled via @user SSO login.

      $ # See the currently enabled profile.
      $ aws configure list
            Name                    Value             Type    Location
            ----                    -----             ----    --------
         profile AWSPowerUserAccess-123456789012              env    ['AWS_DEFAULT_PROFILE', 'AWS_PROFILE']
      access_key     ****************xxxx              sso
      secret_key     ****************xxxx              sso
          region                <not set>             None    None
      $ # SSO profile AWSPowerUserAccess-123456789012 is enabled
      $ # via environment variable.

      $ # See details of the currently enabled AWS account and user/role.
      $ aws sts get-caller-identity

      $ # See if the environment variable has been set.
      $ echo $AWS_PROFILE
      AWSPowerUserAccess-123456789012

      $ unset AWS_PROFILE
      $ # Delete from .bashrc/.zshrc to make the change permanent.
      $ # Now, default profile will be used.
      $ aws configure list
            Name                    Value             Type    Location
            ----                    -----             ----    --------
         profile                <not set>             None    None
         ...
      $ sky check aws -v
      Checking credentials to enable clouds for SkyPilot.
        AWS: enabled [compute, storage]
          Activated account: XXXXXXXXXXXXXXXXXXXXX [account=987654321098]
        ...
      $ # Now AWS account 9876-5432-1098 is enabled via default profile.


- **Profile is not set**. If ``sky check aws`` and ``aws configure list`` cannot find credentials, you may not have a default profile set.

  1. If the environment variable ``AWS_PROFILE`` is set, this profile name will be used.
  2. If there is a profile named ``default``, it will be used.
  3. Otherwise, the profile will not be accessible.

  Even if there is only one profile, it will not be used unless ``AWS_PROFILE`` is set or the profile is named ``default``.

  In AWS CLI v1, you can check ``~/.aws/credentials`` and ``~/.aws/config`` to look for profile names. In AWS CLI v2, you can check from the CLI.

  .. code-block:: console

      $ # AWS CLI v2 only
      $ aws --version
      aws-cli/2.25.6 ...

      $ # List all profiles
      $ aws configure list-profiles
      AWSPowerUserAccess-xxxxxxx
      default

  If there is no ``default`` profile, you can edit the configuration directly:

  .. code-block:: cfg
      :emphasize-lines: 2

      # ~/.aws/config
      [profile default]
      sso_session = my-skypilot-session
      sso_account_id = XXXXXXXXXX
      ...

  .. code-block:: cfg
      :emphasize-lines: 2

      # ~/.aws/config
      [default]
      aws_access_key_id = XXXXXXXXXXXXXXXXXXXX
      aws_secret_access_key = XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

  Or, you can set the ``AWS_PROFILE`` environment variable in your shell config:

  .. code-block:: shell

      # .bashrc / .zshrc
      # Enable AWS profile named "AWSPowerUserAccess-123456789012"
      export AWS_PROFILE='AWSPowerUserAccess-123456789012'


.. toctree::
   :hidden:

   aws-eks-iam-roles
