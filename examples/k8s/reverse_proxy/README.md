# Sky Reverse Proxy for accessing K8s anywhere

The Sky Reverse Proxy is a simple reverse proxy that allows the Sky Control Plane
to access your Kubernetes cluster from anywhere.

## Prerequisites
* Sky Control Plane instance running SSH server
* Access to Kubernetes cluster to install the reverse proxy

## Installation 
### Step 1 - Generate Kubeconfig for the Control Plane
On a machine that has access to the Kubernetes cluster, generate a kubeconfig file for the control plane.

```bash
./generate_cp_kubeconfig.sh
```

Copy over the generated kubeconfig file to the control plane instance:
```bash
rsync -avz kubeconfig.yaml <control-plane>:~/.kube/config
```

On the control plane, configure it to use the `sky-sa` service account by adding this to ~/.sky/config.yaml:
```yaml
kubernetes:
  remote_identity: sky-sa
```


### Step 2 - Install the Reverse Proxy
#### Installation with helm
Install the helm chart with the control plane credentials configured in the `CP_*` environment variables:

```bash
CP_USER=gcpuser
CP_HOST=34.45.42.204
CP_KEY_PATH=~/.ssh/sky-key
CP_PUBLIC_KEY_PATH=~/.ssh/sky-key.pub
helm install my-ssh-reverse-proxy ./ssh-reverse-proxy-chart \
  --set ssh.user=$CP_USER \
  --set ssh.host=$CP_HOST \
  --set secret.id_rsa=$(cat $CP_KEY_PATH | base64) \
  --set secret.id_rsa_pub=$(cat $CP_PUBLIC_KEY_PATH | base64)
```


#### Installation with k8s manifests
Create a secret with the SSH key pair:
```
kubectl create secret generic ssh-key-secret \
    --from-file=id_rsa=/Users/romilb/.ssh/sky-key \
    --from-file=id_rsa.pub=/Users/romilb/.ssh/sky-key.pub
```

Edit `rp.yaml` to manually configure SSH credentials in the environment variables. Then apply the manifest:

```bash
kubectl apply -f rp.yaml
```

### Step 3 - Access the Control Plane
Done! You should now be able to access the Kubernetes API server from your control plane. Try running `kubectl get pods` or `sky launch --cloud kubernetes`.

## Uninstall
To uninstall the reverse proxy, delete the helm release or the k8s resources:

```bash
helm uninstall my-ssh-reverse-proxy
```

```bash
kubectl delete -f rp.yaml
kubectl delete secret ssh-key-secret
```