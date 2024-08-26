# Sky Reverse Proxy for accessing K8s anywhere

The Sky Reverse Proxy is a simple reverse proxy that allows the Sky Control Plane
to access your Kubernetes cluster from anywhere.

## Prerequisites
* Sky Control Plane instance running SSH server
* Access to Kubernetes cluster to install the reverse proxy

## Installation 
### Step 1 - Generate Kubeconfig for the Sky Control Plane
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

### Step 2 - Configure the Inbound SSH Bastion in the Sky Control Plane

We deploy a simple SSH server on the control plane to act as a bastion for the reverse proxy.

Generate keys for the SSH server:
```bash
ssh-keygen -t rsa -b 4096 -f bastion_ssh_key -N ""
```

Prepare the authorized keys file with limited permissions:
```bash
echo 'command="echo \"Port forwarding only allowed\"; exit",no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="localhost:6443" ' > authorized_keys
cat bastion_ssh_key.pub >> authorized_keys
```

Create k8s secret with the SSH keys and authorized keys:
```bash
kubectl create secret generic bastion-ssh-keys --from-file=authorized_keys=authorized_keys --from-file=id_rsa=bastion_ssh_key --from-file=id_rsa.pub=bastion_ssh_key.pub -o yaml --dry-run=client | kubectl apply -f -
```

Deploy the SSH bastion pod:
```bash
kubectl apply -f inbound_bastion.yaml
```

### Step 2 - Install the Reverse Proxy
#### Installation with helm
Install the helm chart with the control plane credentials configured in the `CP_*` environment variables:

```bash
CP_USER=root
CP_HOST=34.94.107.243
CP_KEY_PATH=~/.ssh/bastion_ssh_key
CP_PUBLIC_KEY_PATH=~/.ssh/bastion_ssh_key.pub
export ENCODED_ID_RSA=$(cat $CP_KEY_PATH | base64 | tr -d '\n')
export ENCODED_ID_RSA_PUB=$(cat $CP_PUBLIC_KEY_PATH | base64 | tr -d '\n')
helm install my-ssh-reverse-proxy ssh-reverse-proxy-0.1.0.tgz \
  --set ssh.user=$CP_USER \
  --set ssh.host=$CP_HOST \
  --set secret.id_rsa="$ENCODED_ID_RSA" \
  --set secret.id_rsa_pub="$ENCODED_ID_RSA_PUB"
```

Tip: The Sky CP admin can get the `$CP_HOST` IP address by running `kubectl get svc` and looking for the `EXTERNAL-IP` field.

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

If using bastion, you may need to update IP from localhost to the bastion IP in the kubeconfig file (`kubectl get svc` to get the IP).

```bash

## Uninstall
To uninstall the reverse proxy, delete the helm release or the k8s resources:

```bash
helm uninstall my-ssh-reverse-proxy
```

```bash
kubectl delete -f rp.yaml
kubectl delete secret ssh-key-secret
```