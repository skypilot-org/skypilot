#!/bin/bash
# Builds the Dockerfile_k8s image as the SkyPilot image.
# Optionally, if -p is specified, pushes the image to the registry.
# Uses buildx to build the image for both amd64 and arm64.
# Usage: ./build_image.sh [-p]
# -p: Push the image to the registry

TAG=us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest

# Parse command line arguments
while getopts ":p" opt; do
  case $opt in
    p)
      push=true
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      ;;
  esac
done

# Navigate to the root of the project (inferred from git)
cd "$(git rev-parse --show-toplevel)"

# If push is used, build the image for both amd64 and arm64
if [[ $push ]]; then
  echo "Building and pushing for amd64 and arm64"
  # Push both platforms as one image manifest list
  docker buildx build --push --platform linux/amd64,linux/arm64 -t $TAG -f Dockerfile_k8s ./sky
fi

# Load the right image depending on the architecture of the host machine (Apple Silicon or Intel)
if [[ $(uname -m) == "arm64" ]]; then
  echo "Loading image for arm64 (Apple Silicon etc.)"
  docker buildx build --load --platform linux/arm64 -t us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest -f Dockerfile_k8s ./sky
elif [[ $(uname -m) == "x86_64" ]]; then
  echo "Building for amd64 (Intel CPUs)"
  docker buildx build --load --platform linux/amd64 -t us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest -f Dockerfile_k8s ./sky
else
  echo "Unsupported architecture: $(uname -m)"
  exit 1
fi

echo "Tagging image as skypilot:latest"
docker tag us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest skypilot:latest