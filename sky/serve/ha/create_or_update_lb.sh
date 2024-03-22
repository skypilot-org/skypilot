#!/bin/bash

set -e

service_name=$1
docker_image_repo=$2
new_service_name=$3

# Check if both service name and docker image repo are provided
if [ -z "$service_name" ] || [ -z "$docker_image_repo" ]; then
    echo "Usage: create_or_update_lb.sh <service_name> <docker_image_repo>"
    exit 1
fi

if [ -z "$new_service_name" ]; then
    echo "Starting load balancer for '$service_name'"
    new_service_name=$service_name
else
    echo "Waiting for '$new_service_name' to be ready"
    sky serve status $new_service_name || { echo "Service $new_service_name not found" && exit 1; }
    until sky serve status $new_service_name | grep -q READY; do sleep 5; done
    echo -e "\n\nPointing the load balancer for '$service_name' to the new service '$new_service_name'"
fi

# Prompt for confirmation
read -p "Are you sure you want to continue? (y/n): " confirm
if [[ $confirm != "y" ]]; then
    echo "Operation cancelled"
    exit 0
fi

base_dir=$(git rev-parse --show-toplevel)

docker_image_id=$(date +%Y%m%d%H%M%S)

echo -e "\n\n=== Stage 1: Parsing Endpoints ==="
echo Retrieving endpoint for service $new_service_name
endpoint=$(sky serve status --endpoint $new_service_name)

if [ -z "$endpoint" ]; then
    echo "Service $new_service_name not found"
    exit 1
fi

# Find the controller URL
echo "Retrieving controller URL for service $new_service_name"
controller_url=$(curl $endpoint/-/urls | jq -r '.controller')
# Get the controller port by parsing the URL, e.g. http://localhost:30001 to 30001
# or http://service-url/ports/30001 to 30001
controller_port=$(echo $controller_url | sed -e 's|.*:||' -e 's|/.*||')
controller_endpoint=$(sky status --endpoint $controller_port sky-serve-controller-$(cat ~/.sky/user_hash))
echo -e "\n\nController URL: $controller_endpoint"
# Prepend http:// to the controller endpoint if it doesn't start with http://
if [[ ! $controller_endpoint =~ ^http:// ]]; then
    controller_endpoint="http://$controller_endpoint"
fi

# Build docker image for load balancer
echo -e "\n\n=== Stage 2: Load Balancer Image Building ==="
docker_image_path=${docker_image_repo}/skypilot_lb_ha:$docker_image_id
echo Building the load balancer image and push it to $docker_image_path
docker build -t $docker_image_path -f $base_dir/Dockerfile_ha $base_dir
docker push $docker_image_path

# Create or update the load balancer
# Replace the variables in the load balancer deployment file
echo -e "\n\n=== Stage 3: Load Balancer Deployment ==="
sed -e "s|{{docker_image_path}}|$docker_image_path|g" \
    -e "s|{{controller_endpoint}}|$controller_endpoint|g" \
    -e "s|{{service_name}}|$service_name|g" \
    $base_dir/sky/serve/ha/lb-ha-deployment.yaml > $base_dir/sky/serve/ha/lb-ha-deployment.yaml.tmp

# Apply the load balancer deployment file
kubectl apply -f $base_dir/sky/serve/ha/lb-ha-deployment.yaml.tmp
