# Deploying a SkyPilot API Server on GKE with Okta and Nebius

In this example, we will deploy a SkyPilot API server on a GKE cluster with Okta for authentication.

Infra choices configured in this guide (pick any combination or all):
  * GCP VMs
  * Nebius VMs
  * GKE Kubernetes cluster
  * Nebius Managed Kubernetes cluster

More infra choices (AWS, Lambda Cloud, RunPod, SSH Node Pools, and more) are covered in the [docs](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html#optional-configure-cloud-accounts).

<div align="center">
  <div>
    <img src="https://i.imgur.com/k17TpJU.png" width="600px" alt="Login page">
    <p><em>SkyPilot login with Okta</em></p>
  </div>
</div>

<div align="center">
  <div>
    <img src="https://i.imgur.com/FBSv0PR.png" width="600px" alt="SkyPilot dashboard">
    <p><em>SkyPilot dashboard with running clusters</em></p>
  </div>
</div>


## Prerequisites

* Okta with SkyPilot API server configured as OIDC App (see [docs](https://docs.skypilot.co/en/latest/reference/auth.html#okta-oidc-setup))
* GCP credentials with access to a GKE cluster and permissions to create VMs ([service account with json key](https://docs.skypilot.co/en/latest/cloud-setup/cloud-permissions/gcp.html#service-account))
* Nebius credentials ([service account with json key](https://docs.nebius.com/iam/service-accounts/authorized-keys#create))
* An existing [Nebius Managed Kubernetes cluster](https://docs.nebius.com/kubernetes)
  * Nvidia GPU Operator and Nvidia Device Plugin must be installed on the cluster (Nebius Console -> Applications -> Nvidia {GPU Operator, Device Plugin} -> Deploy)


## Step 1: Collect cloud credentials and variables

Set up the following variables by replacing the values with your own. These variables will be used throughout the guide:
```bash
# Namespace to deploy the API server in and the name of the helm release (can be any string)
NAMESPACE=skypilot
RELEASE_NAME=skypilot

# Okta variables - from Okta console -> Applications -> <SkyPilot App> -> Client ID and Client Secret
OKTA_CLIENT_ID=<okta_client_id>
OKTA_CLIENT_SECRET=<okta_client_secret>
OKTA_ISSUER_URL=<okta_issuer_url> # E.g., https://myorg.okta.com

# GCP variables
GCP_PROJECT_ID=<your_gcp_project_id> # E.g., my-project
GCP_SERVICE_ACCOUNT_JSON=<your_gcp_service_account_json_path> # E.g., $PWD/gcp-service-account.json

# GKE variables. This is the cluster that will host the API server.
GKE_CLUSTER_NAME=<gke_cluster_name> # E.g., mycluster
GKE_ZONE=<gke_zone> # E.g., us-central1

# Nebius variables. This is the external k8s cluster with GPUs.
NEBIUS_CLUSTER_ID=<nebius_cluster_id> # Starts with mk8scluster-; different from cluster name. Can be found in Nebius console.
NEBIUS_TENANT_ID=$(nebius iam tenant list --format json | jq -r '.items[0].metadata.id') # Also available in Nebius console, e.g., abc-123-...
NEBIUS_SERVICE_ACCOUNT_JSON=<your_nebius_service_account_json_path> # E.g., $PWD/nebius-credentials.json

# Temp variables used in the guide, no need to change
TMP_KUBECONFIG=/tmp/sky_kubeconfig
```

### Prepare GCP credentials
Create a secret with the GCP service account json key:

```bash
rm -f $TMP_KUBECONFIG # Remove the file if it exists

# Get GKE credentials
KUBECONFIG=$TMP_KUBECONFIG gcloud container clusters get-credentials $GKE_CLUSTER_NAME --zone $GKE_ZONE
GKE_CONTEXT=$(kubectl config current-context --kubeconfig $TMP_KUBECONFIG)

kubectl create namespace $NAMESPACE --kubeconfig $TMP_KUBECONFIG
kubectl create secret generic gcp-credentials --kubeconfig $TMP_KUBECONFIG --context $GKE_CONTEXT \
  --namespace $NAMESPACE \
  --from-file=gcp-cred.json=$GCP_SERVICE_ACCOUNT_JSON
```

### Prepare Nebius credentials

Create a secret with the Nebius service account json key:

```bash
# Set up Nebius credentials for Nebius CLI auth
kubectl create secret generic nebius-credentials \
  --namespace $NAMESPACE \
  --from-file=credentials.json=$NEBIUS_SERVICE_ACCOUNT_JSON
```

### Prepare Kubernetes credentials: Nebius Managed Kubernetes and GKE

Combine Nebius and GKE credentials into a single kubeconfig and create a secret with the combined kubeconfig:
```bash
# Get Nebius credentials
nebius mk8s cluster get-credentials --id $NEBIUS_CLUSTER_ID --external --kubeconfig $TMP_KUBECONFIG
NEBIUS_CONTEXT=$(kubectl config current-context --kubeconfig $TMP_KUBECONFIG)

# To help SkyPilot identify GPUs on the Nebius cluster, label the nodes with the GPU type. If you already installed the Nvidia Device Plugin, this step will be automatically skipped.
KUBECONFIG=$TMP_KUBECONFIG python -m sky.utils.kubernetes.gpu_labeler --context $NEBIUS_CONTEXT

# Get GKE credentials
KUBECONFIG=$TMP_KUBECONFIG gcloud container clusters get-credentials $GKE_CLUSTER_NAME --zone $GKE_ZONE

# Verify both contexts are available in the kubeconfig
kubectl config get-contexts --kubeconfig $TMP_KUBECONFIG

# Strip exec paths from the kubeconfig to avoid hardcoded paths in the kubeconfig
python -m sky.utils.kubernetes.exec_kubeconfig_converter --input $TMP_KUBECONFIG --output kubeconfig.converted

# Create a secret with the converted kubeconfig
kubectl create secret generic kube-credentials --kubeconfig $TMP_KUBECONFIG --context $GKE_CONTEXT \
  --namespace $NAMESPACE \
  --from-file=config=kubeconfig.converted

# Create a SkyPilot config that allows both contexts (GKE and Nebius) to be used simultaneously:
cat <<EOF > config.yaml
kubernetes:
  allowed_contexts:
    - $NEBIUS_CONTEXT
    - $GKE_CONTEXT
EOF
CONFIG_PATH=$PWD/config.yaml
```


## Step 2: Deploy the API server

Deploy the API server with helm:

```bash
helm repo add skypilot https://helm.skypilot.co
helm repo update

helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
  --namespace $NAMESPACE \
  --create-namespace \
  --set-file apiService.config=$CONFIG_PATH \
  --set ingress.oauth2-proxy.enabled=true \
  --set ingress.oauth2-proxy.oidc-issuer-url=$OKTA_ISSUER_URL \
  --set ingress.oauth2-proxy.client-id=$OKTA_CLIENT_ID \
  --set ingress.oauth2-proxy.client-secret=$OKTA_CLIENT_SECRET \
  --set kubernetesCredentials.useApiServerCluster=false \
  --set kubernetesCredentials.useKubeconfig=true \
  --set kubernetesCredentials.kubeconfigSecretName=kube-credentials \
  --set gcpCredentials.enabled=true \
  --set gcpCredentials.projectId=$GCP_PROJECT_ID \
  --set gcpCredentials.serviceAccountJson=$GCP_SERVICE_ACCOUNT_JSON \
  --set nebiusCredentials.enabled=true \
  --set nebiusCredentials.tenantId=$NEBIUS_TENANT_ID
```

> **💡 Tip:** If you need to reconfigure any of the values, you can simply run `helm upgrade --install ... --reuse-values` with only the changed values. `--reuse-values` will keep the existing values and only update the changed ones.

<details>
<summary>Helm chart args explanation</summary>

Here's an explanation of all the arguments used in the helm chart installation:

| Argument | Description |
|----------|-------------|
| `--namespace $NAMESPACE` | The Kubernetes namespace where SkyPilot will be installed |
| `--create-namespace` | Creates the namespace if it doesn't exist |
| `--set-file apiService.config=$CONFIG_PATH` | Path to the SkyPilot config file that defines allowed Kubernetes contexts |
| `--set ingress.oauth2-proxy.enabled=true` | Enables OAuth2 proxy for authentication |
| `--set ingress.oauth2-proxy.oidc-issuer-url=$OKTA_ISSUER_URL` | URL of the Okta OIDC issuer |
| `--set ingress.oauth2-proxy.client-id=$OKTA_CLIENT_ID` | Okta client ID for OAuth2 authentication |
| `--set ingress.oauth2-proxy.client-secret=$OKTA_CLIENT_SECRET` | Okta client secret for OAuth2 authentication |
| `--set kubernetesCredentials.useApiServerCluster=false` | Disables using the in-cluster authentication for k8s (instead we use kubeconfig) |
| `--set kubernetesCredentials.useKubeconfig=true` | Uses kubeconfig for cluster access |
| `--set kubernetesCredentials.kubeconfigSecretName=kube-credentials` | Name of the secret containing the kubeconfig |
| `--set gcpCredentials.enabled=true` | Enables GCP credentials |
| `--set gcpCredentials.projectId=$GCP_PROJECT_ID` | GCP project ID |
| `--set gcpCredentials.serviceAccountJson=$GCP_SERVICE_ACCOUNT_JSON` | GCP service account JSON credentials |
| `--set nebiusCredentials.enabled=true` | Enables Nebius credentials |
| `--set nebiusCredentials.tenantId=$NEBIUS_TENANT_ID` | Nebius tenant ID |
</details>



## Step 3: Get the endpoint and configure your DNS

```bash
HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE --kubeconfig $TMP_KUBECONFIG --context $GKE_CONTEXT -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
ENDPOINT=http://$HOST
echo $ENDPOINT
```

If you see a blank address, wait a bit and try again. The GCP load balancer takes 1-2min to get an external IP.

Configure your DNS to point to the IP address of the API server. This is required for Okta to verify the `redirect_uri` configured by you in the Okta app. Alternatively, update the `redirect_uri` in the Okta app to use the IP address of the API server.

Try opening the endpoint in a browser. You should see the SkyPilot dashboard login page.

<div align="center">
  <img src="https://i.imgur.com/k17TpJU.png" width="500px" alt="Okta login page">
</div>

After logging in, you should be able to see the configured cloud and kubernetes infra on the dashboard.


<div align="center">
  <div>
    <img src="https://i.imgur.com/0cY1B5A.png" width="600px" alt="infras page">
    <p><em>SkyPilot dashboard showing available infra</em></p>
  </div>
</div>

If the login page shows 503 error, make sure the API server pod is healthy:
```bash
kubectl get pods --namespace $NAMESPACE --kubeconfig $TMP_KUBECONFIG --context $GKE_CONTEXT
```

## Step 4: Configure the CLI and launch your first job

On your client(s), install the SkyPilot CLI:
```bash
pip install -U skypilot-nightly
```

Login to the API server:
```bash
sky api login -e $ENDPOINT # E.g., http://34.42.25.204 or http://sky.yourorg.com
```

A browser will open and you will be redirected to the Okta login page. Login with your Okta credentials. You will receive a token:

<div align="center">
  <img src="https://i.imgur.com/OflAVTp.png" width="500px" alt="Okta token page">
</div>
Copy the token and paste it in the CLI. You should see the following message:

```console
$ sky api login -e http://sky.yourorg.com
Authentication is needed. Please visit this URL setup up the token:

http://sky.yourorg.com/token

Opening browser...
Paste the token:
```

Run `sky check` to verify cloud setup:
```console
$ sky check
...
🎉 Enabled infra 🎉
  GCP [compute, storage]
  Kubernetes [compute]
    Allowed contexts:
    ├── nebius-cluster
    └── gke-cluster
  Nebius [compute]
```

## 🎉 SkyPilot API server is ready to use!

Some commands to try:

* `sky dashboard` to open the dashboard in your browser
* `sky launch -c test --gpus H100:1 -- nvidia-smi` to launch a job with 1 H100 GPU
* `sky show-gpus` to show available GPUs
* `sky status` to see SkyPilot status and infra available

## ✨ Bonus: Infiniband and Nebius shared filesystem

### Configuring Infiniband on Nebius kubernetes cluster

To configure SkyPilot to use infiniband on Nebius:

Set `network_tier: best` in your SkyPilot task YAML resources to enable InfiniBand:

    ```yaml
    resources:
      network_tier: best
    ```

Refer to [Using InfiniBand in Nebius with SkyPilot](https://docs.skypilot.co/en/latest/examples/performance/nebius_infiniband.html)  and [NCCL test example](https://github.com/skypilot-org/skypilot/blob/master/examples/nebius_infiniband/nccl.yaml) for more details.

### Shared storage with Nebius shared filesystem

You can also use [Nebius shared filesystem](https://docs.nebius.com/compute/storage/types#filesystems) with SkyPilot to get high performance data storage for datasets, checkpoints and more across multiple nodes.

When creating a node group on the Nebius console, simply attach your desired shared file system to the node group (``Create Node Group`` -> ``Attach shared filesystem``):

* Ensure ``Auto mount`` is enabled.
* Note the ``Mount tag`` (e.g. ``filesystem-d0``).

<div align="center">
  <img src="https://i.imgur.com/02PhLB5.png" width="50%" alt="Nebius shared filesystem">
</div>

Nebius will automatically mount the shared filesystem to hosts in the node group. You can then use a ``hostPath`` volume to mount the shared filesystem to your SkyPilot pods.

Here's an example of how to use the shared filesystem in a SkyPilot job:

```yaml
resources:
  infra: k8s/nebius-mk8s-nebius-gpu-dev

run: |
  echo "Hello, world!" > /mnt/nfs/hello.txt
  ls -la /mnt/nfs

config:
  kubernetes:
    pod_config:
      spec:
        containers:
          - volumeMounts:
              - mountPath: /mnt/nfs
                name: nebius-sharedfs
        volumes:
          - name: nebius-sharedfs
            hostPath:
              path: /mnt/<mount_tag> # e.g. /mnt/filesystem-d0
              type: Directory
```

> **💡 Tip:** Add the above `config` field to the SkyPilot config (`~/.sky/config.yaml` [global config](https://docs.skypilot.co/en/latest/reference/config.html#config-yaml) or `.sky.yaml` [project config](https://docs.skypilot.co/en/latest/reference/config-sources.html#config-client-project-config)) to have the shared filesystem mounted automatically for all your jobs.
