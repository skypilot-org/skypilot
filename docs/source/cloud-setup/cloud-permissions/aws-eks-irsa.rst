.. _aws-eks-irsa:

Using IAM Roles for S3 Access on EKS
====================================

When running SkyPilot on an EKS cluster (such as SageMaker HyperPod), you can use `IAM Roles for Service Accounts (IRSA) <https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html>`_ to grant pods access to S3 buckets without static AWS credentials.

This is useful when:

- You use AWS SSO and cannot use static credentials
- You want to avoid uploading credentials to pods
- Your organization requires IAM role-based access

Prerequisites
-------------

- An EKS cluster with an OIDC provider
- AWS CLI configured with permissions to create IAM resources
- ``kubectl`` configured with access to the cluster
- An S3 bucket you want to access

Setup
-----

First, set these environment variables for your environment:

.. code-block:: console

    $ # Required: Set these to match your environment
    $ export EKS_CLUSTER_NAME="my-eks-cluster"
    $ export AWS_REGION="us-west-2"
    $ export KUBE_CONTEXT="my-context"
    $ export S3_BUCKET="my-bucket"
    $ export K8S_NAMESPACE="default"

    $ # These will be derived automatically
    $ export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    $ export IAM_POLICY_NAME="SkyPilotS3Access-${S3_BUCKET}"
    $ export IAM_ROLE_NAME="SkyPilotS3Role-${S3_BUCKET}"

    $ # Do not change this unless you're using a custom service account for SkyPilot pods
    $ export K8S_SERVICE_ACCOUNT="skypilot-service-account"

Step 1: Get OIDC provider information
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ # Get the OIDC issuer URL
    $ OIDC_ISSUER=$(aws eks describe-cluster \
        --name $EKS_CLUSTER_NAME \
        --region $AWS_REGION \
        --query "cluster.identity.oidc.issuer" \
        --output text)

    $ # Extract the OIDC provider ID
    $ export OIDC_ID=$(echo $OIDC_ISSUER | sed 's|https://oidc.eks.'"$AWS_REGION"'.amazonaws.com/id/||')
    $ export OIDC_PROVIDER="oidc.eks.${AWS_REGION}.amazonaws.com/id/${OIDC_ID}"

Step 2: Register OIDC provider in IAM
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check if the OIDC provider is already registered:

.. code-block:: console

    $ aws iam list-open-id-connect-providers \
        --query "OpenIDConnectProviderList[*].Arn" \
        --output text | grep -i $OIDC_ID

If not found, create it:

.. code-block:: console

    $ # Get the SSL thumbprint
    $ THUMBPRINT=$(echo | openssl s_client \
        -servername oidc.eks.${AWS_REGION}.amazonaws.com \
        -showcerts \
        -connect oidc.eks.${AWS_REGION}.amazonaws.com:443 2>/dev/null \
        | openssl x509 -fingerprint -sha1 -noout \
        | sed 's/://g' \
        | sed 's/.*=//g')

    $ # Create the OIDC identity provider
    $ aws iam create-open-id-connect-provider \
        --url https://${OIDC_PROVIDER} \
        --client-id-list sts.amazonaws.com \
        --thumbprint-list $THUMBPRINT

Step 3: Create IAM policy for S3 access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ cat > /tmp/skypilot-s3-policy.json << EOF
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation"
                ],
                "Resource": [
                    "arn:aws:s3:::${S3_BUCKET}",
                    "arn:aws:s3:::${S3_BUCKET}/*"
                ]
            }
        ]
    }
    EOF

    $ aws iam create-policy \
        --policy-name $IAM_POLICY_NAME \
        --policy-document file:///tmp/skypilot-s3-policy.json

    $ export IAM_POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${IAM_POLICY_NAME}"

Step 4: Create IAM role with trust policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ cat > /tmp/trust-policy.json << EOF
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}"
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "${OIDC_PROVIDER}:sub": "system:serviceaccount:${K8S_NAMESPACE}:${K8S_SERVICE_ACCOUNT}",
                        "${OIDC_PROVIDER}:aud": "sts.amazonaws.com"
                    }
                }
            }
        ]
    }
    EOF

    $ aws iam create-role \
        --role-name $IAM_ROLE_NAME \
        --assume-role-policy-document file:///tmp/trust-policy.json

    $ export IAM_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME}"

    $ # Attach the S3 policy to the role
    $ aws iam attach-role-policy \
        --role-name $IAM_ROLE_NAME \
        --policy-arn $IAM_POLICY_ARN

Step 5: Annotate the Kubernetes service account
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Annotate the service account with the IAM role ARN:

.. code-block:: console

    $ kubectl --context $KUBE_CONTEXT annotate serviceaccount $K8S_SERVICE_ACCOUNT \
        --namespace $K8S_NAMESPACE \
        eks.amazonaws.com/role-arn=$IAM_ROLE_ARN \
        --overwrite


If the service account does not already exist, create it by running a ``sky launch``.

Step 6: Configure SkyPilot
~~~~~~~~~~~~~~~~~~~~~~~~~~

Add the following to your ``~/.sky/config.yaml`` to prevent SkyPilot from uploading local AWS credentials:

.. code-block:: yaml

    aws:
      remote_identity: NO_UPLOAD

See :ref:`config-yaml-aws-remote-identity` for more details.

Step 7: Test the setup
~~~~~~~~~~~~~~~~~~~~~~

Launch a SkyPilot cluster with S3 mount:

.. code-block:: yaml

    resources:
      infra: kubernetes

    file_mounts:
      /s3/data:
        source: s3://my-bucket/
        mode: MOUNT

    run: |
      ls -la /s3/data

Launch the job:

.. code-block:: bash

    $ sky launch -c test-s3-mount --infra kubernetes task.yaml
    # Job will print the contents of the S3 bucket

Troubleshooting
---------------

- **"No OpenIDConnect provider found"**

  This error means the OIDC provider is not registered in IAM. Follow Step 2 above to create it.

- **"NoCredentialProviders: no valid providers in chain"**  

  - Ensure ``aws.remote_identity: NO_UPLOAD`` is set in ``~/.sky/config.yaml``
  - Verify the service account has the correct annotation
  - Restart pods after making changes (``sky down`` then ``sky launch``)

- **Manually verify IRSA is working**    

  SSH into the pod and check if AWS credentials are injected:

  .. code-block:: console

      $ ssh <cluster-name> # Or kubectl exec -it <pod-name> -n <namespace> -- bash
      $ pip install awscli
      $ aws s3 ls s3://${S3_BUCKET}/
