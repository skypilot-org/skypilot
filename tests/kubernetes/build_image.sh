#!/bin/bash
# Builds the Dockerfile_k8s image as the SkyPilot image.
# Optionally, if -p is specified, pushes the image to the registry.
# Uses buildx to build the image for both amd64 and arm64.
# If -p flag is specified, pushes the image to the registry.
# If -g flag is specified, builds the GPU image in Dockerfile_k8s_gpu. GPU image is built only for amd64.
# Usage: ./build_image.sh [-p] [-g]
# -p: Push the image to the registry
# -g: Build the GPU image

TAG=us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot

push=false
gpu=false

# Parse command line arguments
while getopts ":pg" opt; do
  case ${opt} in
    p )
      push=true
      ;;
    g )
      gpu=true
      ;;
    \? )
      echo "Usage: ./build_image.sh [-p] [-g]"
      echo "-p: Push the image to the registry"
      echo "-g: Build the GPU image"
      exit 1
      ;;
  esac
done

# Add -gpu to the tag if the GPU image is being built
if [[ $gpu == "true" ]]; then
  TAG=$TAG-gpu:latest
else
  TAG=$TAG:latest
fi

# Shift off the options
shift $((OPTIND-1))


# Navigate to the root of the project (inferred from git)
cd "$(git rev-parse --show-toplevel)"

# If push is used, build the image for both amd64 and arm64
if [[ $push == "true" ]]; then
  # If gpu is used, build the GPU image
  if [[ $gpu == "true" ]]; then
    echo "Building and pushing GPU image for amd64: $TAG"
    docker buildx build --push --platform linux/amd64 -t $TAG -f Dockerfile_k8s_gpu ./sky
  else
    echo "Building and pushing CPU image for amd64 and arm64: $TAG"
    docker buildx build --push --platform linux/arm64,linux/amd64 -t $TAG -f Dockerfile_k8s ./sky
  fi
fi

# Load the right image depending on the architecture of the host machine (Apple Silicon or Intel)
if [[ $(uname -m) == "arm64" ]]; then
  echo "Loading image for arm64 (Apple Silicon etc.): $TAG"
  docker buildx build --load --platform linux/arm64 -t $TAG -f Dockerfile_k8s ./sky
elif [[ $(uname -m) == "x86_64" ]]; then
  echo "Building for amd64 (Intel CPUs): $TAG"
  docker buildx build --load --platform linux/amd64 -t $TAG -f Dockerfile_k8s ./sky
else
  echo "Unsupported architecture: $(uname -m)"
  exit 1
fi

echo "Tagging image."
if [[ $gpu ]]; then
  docker tag $TAG skypilot:latest-gpu
else
  docker tag $TAG skypilot:latest
fi
