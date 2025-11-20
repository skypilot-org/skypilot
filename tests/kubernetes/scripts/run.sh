# TODO(kbrgl): Fix secret creation since all SSH keys are now stored in one secret object.
kubectl create secret generic ssh-key-secret --from-file=ssh-publickey=/Users/romilb/.ssh/sky-key.pub
kubectl apply -f skypilot_ssh_k8s_deployment.yaml
# Use kubectl describe service skypilot-service to get the port of the service
kubectl describe service skypilot-service | grep NodePort
echo Run the following command to ssh into the container:
echo ssh sky@127.0.0.1 -p port -i ~/.ssh/sky-key
