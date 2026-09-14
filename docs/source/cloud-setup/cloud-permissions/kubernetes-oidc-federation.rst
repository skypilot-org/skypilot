.. _kubernetes-oidc-federation:

Passwordless Cloud Access for Kubernetes Pods (OIDC Federation)
===============================================================

This page covers how to grant SkyPilot pods access to cloud resources (GCS, S3, Azure Blob, etc.) **without uploading static credentials** — using OIDC federation between your Kubernetes cluster and the target cloud's IAM.

This works for both:

- **Same-cloud setups** (e.g., EKS pods accessing S3, GKE pods accessing GCS), and
- **Cross-cloud setups** (e.g., pods on a Nebius / on-prem / EKS cluster accessing GCS, or pods on a GKE cluster accessing S3).

For EKS-specific same-cloud setup against S3, see also :ref:`aws-eks-iam-roles`, which covers the simpler EKS Pod Identity path.


When to use this
----------------

Use OIDC federation when any of the following apply:

- You don't want users to upload long-lived cloud credential files (e.g., GCP service account keys, AWS access keys) into SkyPilot pods.
- Your security policy mandates short-lived, federation-issued credentials.
- Pods run on a Kubernetes cluster in one cloud / on-prem and need to access resources in a different cloud (e.g., Nebius-hosted pod reading from GCS).
- Different SkyPilot workspaces / teams need to be scoped to different cloud identities.

If you control both the cluster and the cloud project, this is the recommended setup.


How OIDC federation works
-------------------------

The pieces:

1. **The Kubernetes API server acts as an OIDC issuer.** It signs ServiceAccount tokens with its own key and exposes a public JWKS endpoint so external systems can verify those signatures.

2. **Pods receive a projected ServiceAccount token (JWT).** Mounted into the pod by the kubelet via a ``projected.serviceAccountToken`` volume. The JWT's ``sub`` claim is ``system:serviceaccount:<namespace>:<sa-name>``.

3. **The cloud's IAM is configured to trust the cluster's OIDC issuer.** Done once per cluster: register the issuer URL + tell IAM which (issuer, subject) pairs map to which cloud identity.

4. **The cloud SDK in the pod exchanges the JWT for short-lived cloud credentials** via the cloud's STS endpoint. No long-lived secrets, no key files.

Runtime flow inside a pod::

    1. App calls cloud SDK (e.g., google.cloud.storage.Client())
    2. SDK reads the projected JWT from /var/run/secrets/.../token
    3. SDK calls cloud STS, presenting the JWT
    4. STS verifies the signature against the cluster's JWKS
    5. STS checks the JWT's sub matches the configured binding
    6. STS returns short-lived cloud credentials
    7. SDK uses those credentials (and auto-refreshes before expiry)


Choosing an approach
--------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - **Managed (same-cloud)**
     - **Generic OIDC federation (cross-cloud)**
   * - When to use
     - K8s cluster and target cloud are the same provider
     - K8s cluster runs anywhere; needs access to any cloud
   * - GCP
     - GKE Workload Identity (for GKE clusters)
     - Workload Identity Federation (any cluster)
   * - AWS
     - EKS Pod Identity / IRSA (for EKS clusters)
     - OIDC Identity Provider (any cluster)
   * - Azure
     - AKS Workload Identity (for AKS clusters)
     - Federated Identity Credentials (any cluster)
   * - Setup complexity
     - Lower (issuer already trusted)
     - Slightly higher (publish OIDC discovery, register issuer)

The generic and managed forms produce the same end result: a KSA-scoped, short-lived cloud credential. Pick the managed form when available; pick the generic form for cross-cloud cases.


Prerequisite: make Kubernetes OIDC discovery reachable
------------------------------------------------------

The target cloud's STS must be able to **fetch your cluster's OIDC discovery document and JWKS** to verify JWT signatures.

**For managed clusters (GKE / EKS / AKS):** Already done. The cloud provider exposes the discovery endpoint on a public URL (e.g., ``https://oidc.eks.<region>.amazonaws.com/id/<cluster-id>``).

**For self-managed clusters (Nebius, on-prem, k3s, etc.):** Publish the discovery document and JWKS to a public location and set the kube-apiserver's ``--service-account-issuer`` to that URL.

The most common pattern is to publish to a public object storage bucket:

.. code-block:: console

    $ # 1. Fetch the discovery document and JWKS from your cluster
    $ kubectl get --raw /.well-known/openid-configuration > openid-configuration
    $ kubectl get --raw /openid/v1/jwks > jwks

    $ # 2. Upload to a public bucket (example with GCS)
    $ gsutil cp openid-configuration gs://my-cluster-oidc/.well-known/openid-configuration
    $ gsutil cp jwks gs://my-cluster-oidc/openid/v1/jwks
    $ gsutil iam ch allUsers:objectViewer gs://my-cluster-oidc

    $ # 3. Confirm the discovery doc returns the same URL as its issuer
    $ curl https://storage.googleapis.com/my-cluster-oidc/.well-known/openid-configuration
    {
      "issuer": "https://storage.googleapis.com/my-cluster-oidc",
      "jwks_uri": "https://storage.googleapis.com/my-cluster-oidc/openid/v1/jwks",
      ...
    }

    $ # 4. Configure the kube-apiserver to issue tokens with this issuer
    $ # (Add to kube-apiserver flags; method depends on your cluster's installer)
    $ # --service-account-issuer=https://storage.googleapis.com/my-cluster-oidc

.. note::

    The ``issuer`` field inside the discovery document **must exactly match** the URL where it is hosted, and must match what the kube-apiserver stamps into JWTs. Mismatched issuers are the most common federation setup failure.


GCP: Workload Identity Federation
---------------------------------

Use this to grant pods on **any** Kubernetes cluster access to GCP resources (GCS, BigQuery, etc.).

Step 1: Create a Workload Identity Pool and OIDC Provider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ # Set these to match your environment
    $ export GCP_PROJECT_ID="my-project"
    $ export GCP_PROJECT_NUMBER=$(gcloud projects describe $GCP_PROJECT_ID --format="value(projectNumber)")
    $ export POOL_ID="k8s-pool"
    $ export PROVIDER_ID="k8s-provider"
    $ export ISSUER_URL="https://storage.googleapis.com/my-cluster-oidc"   # Or your cluster's issuer

    $ gcloud iam workload-identity-pools create $POOL_ID \
        --project=$GCP_PROJECT_ID \
        --location=global \
        --display-name="Kubernetes pool"

    $ gcloud iam workload-identity-pools providers create-oidc $PROVIDER_ID \
        --project=$GCP_PROJECT_ID \
        --location=global \
        --workload-identity-pool=$POOL_ID \
        --issuer-uri=$ISSUER_URL \
        --attribute-mapping="google.subject=assertion.sub" \
        --attribute-condition="assertion.sub.startsWith('system:serviceaccount:')"

Step 2: Bind a GCP Service Account to a Kubernetes ServiceAccount
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ # The GCP SA that has the cloud permissions you want pods to inherit
    $ export GCP_SA_EMAIL="team-a@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

    $ # The Kubernetes ServiceAccount that pods will run as
    $ export K8S_NAMESPACE="default"
    $ export K8S_SA="team-a-sa"

    $ gcloud iam service-accounts add-iam-policy-binding $GCP_SA_EMAIL \
        --project=$GCP_PROJECT_ID \
        --role="roles/iam.workloadIdentityUser" \
        --member="principal://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/subject/system:serviceaccount:${K8S_NAMESPACE}:${K8S_SA}"

Step 3: Grant the GCP SA the actual cloud permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For example, to read/write a GCS bucket:

.. code-block:: console

    $ gsutil iam ch serviceAccount:${GCP_SA_EMAIL}:objectAdmin gs://my-bucket

Step 4: Mount the credential JSON into pods
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

GCP SDKs read federation config from a JSON file referenced by ``GOOGLE_APPLICATION_CREDENTIALS``. The simplest approach is to create the credentials file via ``gcloud`` and bake it into the SkyPilot config so it gets mounted into every pod.

Generate the credentials JSON locally:

.. code-block:: console

    $ gcloud iam workload-identity-pools create-cred-config \
        projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID} \
        --service-account=$GCP_SA_EMAIL \
        --credential-source-file=/var/run/secrets/tokens/gcp-token \
        --credential-source-type=text \
        --output-file=/tmp/gcp-creds.json

Add a ``pod_config`` block to your SkyPilot ``~/.sky/config.yaml`` so every SkyPilot pod gets a projected token at ``/var/run/secrets/tokens/gcp-token`` with the right audience, and ``GOOGLE_APPLICATION_CREDENTIALS`` pointing at the creds file:

.. code-block:: yaml

    kubernetes:
      remote_identity: team-a-sa     # The KSA bound above
      pod_config:
        spec:
          serviceAccountName: team-a-sa
          containers:
            - env:
                - name: GOOGLE_APPLICATION_CREDENTIALS
                  value: /etc/gcp/creds.json
              volumeMounts:
                - name: gcp-token
                  mountPath: /var/run/secrets/tokens
                - name: gcp-creds
                  mountPath: /etc/gcp
                  readOnly: true
          volumes:
            - name: gcp-token
              projected:
                sources:
                  - serviceAccountToken:
                      path: gcp-token
                      audience: //iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL_ID/providers/PROVIDER_ID
                      expirationSeconds: 3600
            - name: gcp-creds
              configMap:
                name: gcp-creds      # Pre-create this ConfigMap with the creds JSON


AWS: OIDC Identity Provider (cross-cloud)
-----------------------------------------

For EKS-on-AWS, prefer :ref:`aws-eks-iam-roles` (EKS Pod Identity, simpler).

For pods on a non-EKS cluster (Nebius, on-prem, GKE) needing AWS access, the steps are the same as :ref:`aws-eks-iam-roles` Option 2 (IRSA), with one substitution: the OIDC issuer is **your cluster's public discovery URL** instead of the EKS-managed one.

Step 1: Register your cluster's OIDC provider in IAM
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ export OIDC_ISSUER="storage.googleapis.com/my-cluster-oidc"   # No scheme prefix
    $ export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

    $ # Fetch the SSL thumbprint for the issuer's host
    $ THUMBPRINT=$(echo | openssl s_client -servername storage.googleapis.com \
        -showcerts -connect storage.googleapis.com:443 2>/dev/null \
        | openssl x509 -fingerprint -sha1 -noout \
        | sed 's/://g' | sed 's/.*=//g')

    $ aws iam create-open-id-connect-provider \
        --url "https://${OIDC_ISSUER}" \
        --client-id-list sts.amazonaws.com \
        --thumbprint-list $THUMBPRINT

Step 2: Create an IAM role with a trust policy tied to your KSA
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ export K8S_NAMESPACE="default"
    $ export K8S_SA="team-a-sa"

    $ cat > /tmp/trust-policy.json <<EOF
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {
          "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER}"
        },
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
          "StringEquals": {
            "${OIDC_ISSUER}:aud": "sts.amazonaws.com",
            "${OIDC_ISSUER}:sub": "system:serviceaccount:${K8S_NAMESPACE}:${K8S_SA}"
          }
        }
      }]
    }
    EOF

    $ aws iam create-role \
        --role-name SkyPilot-${K8S_SA} \
        --assume-role-policy-document file:///tmp/trust-policy.json

    $ # Attach whatever managed/inline policies grant the actual access (S3, etc.)
    $ aws iam attach-role-policy \
        --role-name SkyPilot-${K8S_SA} \
        --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

Step 3: Mount the AWS web identity token into pods
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The AWS SDK reads ``AWS_WEB_IDENTITY_TOKEN_FILE`` and ``AWS_ROLE_ARN`` from the environment. Mount a projected token and set both env vars via SkyPilot's ``pod_config``:

.. code-block:: yaml

    kubernetes:
      remote_identity: team-a-sa
      pod_config:
        spec:
          serviceAccountName: team-a-sa
          containers:
            - env:
                - name: AWS_WEB_IDENTITY_TOKEN_FILE
                  value: /var/run/secrets/tokens/aws-token
                - name: AWS_ROLE_ARN
                  value: arn:aws:iam::123456789012:role/SkyPilot-team-a-sa
              volumeMounts:
                - name: aws-token
                  mountPath: /var/run/secrets/tokens
          volumes:
            - name: aws-token
              projected:
                sources:
                  - serviceAccountToken:
                      path: aws-token
                      audience: sts.amazonaws.com
                      expirationSeconds: 3600


Azure: Federated Identity Credentials
-------------------------------------

Use this to grant pods on any K8s cluster access to Azure resources via a User Assigned Managed Identity.

Step 1: Create a User Assigned Managed Identity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ export AZ_RG="my-resource-group"
    $ export AZ_IDENTITY="team-a-identity"
    $ export AZ_SUBSCRIPTION=$(az account show --query id -o tsv)

    $ az identity create --name $AZ_IDENTITY --resource-group $AZ_RG

    $ export AZ_CLIENT_ID=$(az identity show --name $AZ_IDENTITY --resource-group $AZ_RG --query clientId -o tsv)
    $ export AZ_TENANT_ID=$(az account show --query tenantId -o tsv)

Step 2: Add a federated credential bound to your KSA
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ export K8S_NAMESPACE="default"
    $ export K8S_SA="team-a-sa"
    $ export ISSUER_URL="https://storage.googleapis.com/my-cluster-oidc"

    $ az identity federated-credential create \
        --name "${K8S_SA}-fed" \
        --identity-name $AZ_IDENTITY \
        --resource-group $AZ_RG \
        --issuer "$ISSUER_URL" \
        --subject "system:serviceaccount:${K8S_NAMESPACE}:${K8S_SA}" \
        --audiences "api://AzureADTokenExchange"

Step 3: Grant the managed identity Azure RBAC roles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    $ az role assignment create \
        --role "Storage Blob Data Contributor" \
        --assignee $AZ_CLIENT_ID \
        --scope /subscriptions/$AZ_SUBSCRIPTION/resourceGroups/$AZ_RG/providers/Microsoft.Storage/storageAccounts/myaccount

Step 4: Mount the federation env vars into pods
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    kubernetes:
      remote_identity: team-a-sa
      pod_config:
        spec:
          serviceAccountName: team-a-sa
          containers:
            - env:
                - name: AZURE_CLIENT_ID
                  value: <AZ_CLIENT_ID>
                - name: AZURE_TENANT_ID
                  value: <AZ_TENANT_ID>
                - name: AZURE_FEDERATED_TOKEN_FILE
                  value: /var/run/secrets/tokens/azure-token
                - name: AZURE_AUTHORITY_HOST
                  value: https://login.microsoftonline.com/
              volumeMounts:
                - name: azure-token
                  mountPath: /var/run/secrets/tokens
          volumes:
            - name: azure-token
              projected:
                sources:
                  - serviceAccountToken:
                      path: azure-token
                      audience: api://AzureADTokenExchange
                      expirationSeconds: 3600


Wiring it into SkyPilot
-----------------------

Once the cluster-admin / cloud-IAM setup above is done, SkyPilot only needs to know **which KSA each pod should run as**. There are three scopes:

**Per-context (global):** applies to every SkyPilot task that lands on a given kubeconfig context.

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      context_configs:
        my-context:
          remote_identity: team-a-sa

**Per-context (dict form):** if your config spans multiple contexts:

.. code-block:: yaml

    kubernetes:
      remote_identity:
        ctx-1: team-a-sa
        ctx-2: team-a-sa-other-cluster

**Per-workspace** (planned): pick a SA on a per-workspace basis without minting a kubeconfig context per team. Tracked in `SKY-5430 <https://linear.app/skypilot/issue/SKY-5430>`_.

.. note::

    Also set ``remote_identity: NO_UPLOAD`` for the target cloud (``aws``, ``gcp``, or equivalent) at the top level of your config to prevent SkyPilot from uploading local credential files into pods. See :ref:`config-yaml-aws-remote-identity` for details.


Verifying it works
------------------

Launch a SkyPilot task that exercises the cloud SDK:

.. code-block:: yaml

    # test-federation.yaml
    resources:
      infra: kubernetes

    run: |
      # GCP example
      python -c "from google.cloud import storage; print(list(storage.Client().list_buckets())[:3])"

.. code-block:: console

    $ sky launch -c test-fed test-federation.yaml

You can also SSH into the pod and inspect the credential chain directly:

.. code-block:: console

    $ ssh test-fed
    $ ls -la /var/run/secrets/tokens/   # Projected JWT should be there
    $ cat /var/run/secrets/tokens/gcp-token | cut -d. -f2 | base64 -d 2>/dev/null | jq
    # Expect: { "iss": "...", "sub": "system:serviceaccount:...", "aud": [...] }


Troubleshooting
---------------

**"invalid_target" / "Permission denied"**

The trust binding between the KSA and the cloud identity is misconfigured. Verify the ``sub`` claim in the JWT (``system:serviceaccount:<namespace>:<sa>``) exactly matches what the cloud's trust policy / federated credential expects.

**"signature verification failed" / "JWKS not found"**

The cloud's STS cannot reach your cluster's JWKS endpoint. For self-managed clusters, confirm the ``jwks_uri`` in your published ``openid-configuration`` resolves to a public URL returning the cluster's signing keys.

**"audience does not match"**

The ``audience`` requested in the projected token doesn't match what the cloud's STS expects. Common values:

- GCP: ``//iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/<pool>/providers/<provider>``
- AWS: ``sts.amazonaws.com``
- Azure: ``api://AzureADTokenExchange``

**"token is expired"**

Cloud SDKs usually auto-refresh, but if your application caches credentials, increase ``expirationSeconds`` on the projected token or rebuild the SDK client periodically. K8s caps projected token lifetime at ``--service-account-max-token-expiration`` on the apiserver (default 1h).

**Issuer URL mismatch**

The ``issuer`` claim in JWTs minted by the kube-apiserver must exactly match the URL where the OIDC discovery document is hosted, and must match what was registered with the cloud's IAM. ``http://`` vs ``https://`` and trailing slashes both count.
