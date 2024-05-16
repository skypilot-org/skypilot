#!/bin/bash
# Builds the Dockerfile_k8s image as the SkyPilot image.
# Optionally, if -p is specified, pushes the image to the registry.
# Uses buildx to build the image for both amd64 and arm64.
# If -p flag is specified, pushes the image to the registry.
# If -g flag is specified, builds the GPU image in Dockerfile_k8s_gpu. GPU image is built only for amd64.
# If -l flag is specified, uses the latest tag instead of the date tag. Date tag is of the form YYYYMMDD.
# Usage: ./build_image.sh [-p] [-g]
# -p: Push the image to the registry
# -g: Build the GPU image
# -l: Use latest tag

TAG=us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot

push=false
gpu=false
latest=false

# Parse command line arguments
while getopts ":pgl" opt; do
  case ${opt} in
    p )
      push=true
      ;;
    g )
      gpu=true
      ;;
    l )
      latest=true
      ;;
    \? )
      echo "Usage: ./build_image.sh [-p] [-g] [-l]"
      echo "-p: Push the image to the registry"
      echo "-g: Build the GPU image"
      echo "-l: Use latest tag instead of the date tag"
      exit 1
      ;;
  esac
done

echo "Options:"
echo "Push: $push"
echo "GPU: $gpu"
echo "Latest: $latest"

# Set the version tag. If the latest flag is used, use the latest tag
if [[ $latest == "true" ]]; then
  VERSION_TAG=latest
else
  VERSION_TAG=$(date +%Y%m%d)
fi

# Add -gpu to the tag if the GPU image is being built
if [[ $gpu == "true" ]]; then
  TAG=$TAG-gpu:${VERSION_TAG}
else
  TAG=$TAG:${VERSION_TAG}
fi

echo "Building image: $TAG"

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
  docker tag $TAG skypilot-gpu:latest
else
  docker tag $TAG skypilot:latest
fi
