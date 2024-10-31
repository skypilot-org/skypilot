#!/bin/bash
# Builds the Dockerfile_k8s image as the SkyPilot image.
# Uses buildx to build the image for both amd64 and arm64.
# Usage: ./skypilot-k8s-image.sh [-p] [-g] [-l] [-r region]
# -p: Push the image to the registry
# -g: Builds the GPU image in Dockerfile_k8s_gpu. GPU image is built only for amd64
# -l: Use latest tag instead of the date tag. Date tag is of the form YYYYMMDD
# -r: Specify the region to be us, europe or asia
region=us
push=false
gpu=false
latest=false

# Parse command line arguments
OPTSTRING=":pglr:"
while getopts ${OPTSTRING} opt; do
  case ${opt} in
    p)
      push=true
      ;;
    g)
      gpu=true
      ;;
    l)
      latest=true
      ;;
    r)
      region=${OPTARG}
      ;;
    ?)
      echo "Usage: ./build_image.sh [-p] [-g] [-l] [-r region]"
      echo "-p: Push the image to the registry"
      echo "-g: Build the GPU image"
      echo "-l: Use latest tag instead of the date tag"
      echo "-r: Specify the region to be us, europe or asia"
      exit 1
      ;;
  esac
done

echo "Options:"
echo "Push: $push"
echo "GPU: $gpu"
echo "Latest: $latest"
echo "Region: $region"

TAG=$region-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot

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
