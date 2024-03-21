#!/bin/bash
# Quick and dirty network performance benchmark to measure client->K8s perf

# Check if iperf3 is installed
if ! command -v iperf3 &> /dev/null
then
    echo "iperf3 could not be found. Please install iperf3 and try again."
    exit 1
fi

# Create iperf3 server pod and service
echo "Creating iperf3 server pod and service in the Kubernetes cluster..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: iperf3-server
  labels:
    app: iperf3
spec:
  containers:
  - name: iperf3-server
    image: networkstatic/iperf3
    args: ["-s"]
    ports:
    - containerPort: 5201
---
apiVersion: v1
kind: Service
metadata:
  name: iperf3-server
spec:
  type: ClusterIP
  selector:
    app: iperf3
  ports:
  - port: 5201
    targetPort: 5201
EOF

# Wait for the pod to be ready
echo "Waiting for iperf3-server pod to be ready..."
kubectl wait --for=condition=ready pod/iperf3-server --timeout=60s

# Set up port-forwarding to the iperf3 server service
echo "Setting up port-forwarding to the iperf3 server..."
kubectl port-forward service/iperf3-server 5201:5201 &
PORT_FORWARD_PID=$!
echo "Port-forwarding PID: $PORT_FORWARD_PID"

# Wait a bit to ensure the port-forwarding is fully operational
sleep 5

# IP or hostname of the Kubernetes cluster's API server
APISERVER_URL=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

# Benchmark network latency to the API server
echo "Measuring latency to the Kubernetes API server..."
ping -c 5 $(echo $APISERVER_URL | sed -e 's|^[^/]*//||' -e 's|:.*$||')

# Benchmark network bandwidth to the iperf3 server within the cluster
echo "Measuring bandwidth to the iperf3 server within the Kubernetes cluster via port-forwarding..."
iperf3 -c 127.0.0.1 -t 20 -

# Benchmark API server response time
echo "Measuring Kubernetes API server response time..."
start_time=$(date +%s%N)
curl -s -o /dev/null -w "%{time_total}\n" -k $APISERVER_URL/version
end_time=$(date +%s%N)
elapsed_time=$(echo "scale=3; ($end_time - $start_time) / 1000000000" | bc)

echo "Benchmark completed."

# Cleanup
# Kill the port-forwarding process
kill $PORT_FORWARD_PID
echo "Port-forwarding process terminated."

# Cleanup pod and service
echo "Cleaning up iperf3 server pod and service..."
kubectl delete pod iperf3-server
kubectl delete service iperf3-server