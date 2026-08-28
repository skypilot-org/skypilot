#!/bin/bash
# Builds the Dockerfile_k8s image as the SkyPilot image.
# Uses buildx to build the image for both amd64 and arm64.
#
# Note: Running `docker run --rm --privileged multiarch/qemu-user-static --reset -p yes`
# first may solve some segmentation faults issue with QEMU when building the
# image across architectures.
#
# Usage: ./skypilot-k8s-image.sh [-p] [-g] [-l] [-r region] [-b base_image] [-s suffix]
# -p: Push the image to the registry
# -g: Builds the GPU image in Dockerfile_k8s_gpu. GPU image is built only for amd64
# -l: Use latest tag instead of the date tag. Date tag is of the form YYYYMMDDHHMM
# -r: Specify the region to be us, europe or asia
# -b: Build on a different base image (Dockerfile BASE_IMAGE arg), e.g.
#     ubuntu:26.04 or nvidia/cuda:12.8.1-runtime-ubuntu26.04. Defaults to the
#     BASE_IMAGE pinned in the Dockerfile, so a plain invocation always tracks
#     the base we ship rather than an older one.
# -s: Append a suffix to the version tag, e.g. -s ubuntu2404 -> <date>-ubuntu2404.
#     Defaults to the base image's Ubuntu release, so a dated tag says what it
#     was built on. The default is dropped for -l, which publishes the shared
#     `latest` tag; an explicit -s still applies.
region=us
push=false
gpu=false
latest=false
base_image=""
suffix=""
suffix_set=false

# Parse command line arguments
OPTSTRING=":pglr:b:s:"
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
    b)
      base_image=${OPTARG}
      ;;
    s)
      suffix=${OPTARG}
      suffix_set=true
      ;;
    ?)
      echo "Usage: ./skypilot-k8s-image.sh [-p] [-g] [-l] [-r region] [-b base_image] [-s suffix]"
      echo "-p: Push the image to the registry"
      echo "-g: Build the GPU image"
      echo "-l: Use latest tag instead of the date tag"
      echo "-r: Specify the region to be us, europe or asia"
      echo "-b: Build on a different base image (BASE_IMAGE build arg)"
      echo "-s: Append a suffix to the version tag"
      exit 1
      ;;
  esac
done

# Shift off the options
shift $((OPTIND-1))

if [[ $gpu == "true" ]]; then
  DOCKERFILE=Dockerfile_k8s_gpu
else
  DOCKERFILE=Dockerfile_k8s
fi

# Navigate to the root of the project (inferred from git)
cd "$(git rev-parse --show-toplevel)"

BUILD_ARGS=()
if [[ -n $base_image ]]; then
  BUILD_ARGS+=(--build-arg "BASE_IMAGE=$base_image")
  effective_base=$base_image
else
  # The Dockerfile's own default is the base we ship, so read it back rather
  # than duplicating it here: the printed base and the derived tag suffix then
  # cannot drift from what is actually built.
  effective_base=$(sed -n 's/^ARG BASE_IMAGE=//p' "$DOCKERFILE" | head -1)
fi

echo "Options:"
echo "Push: $push"
echo "GPU: $gpu"
echo "Latest: $latest"
echo "Region: $region"
echo "Base image: ${effective_base:-<unknown>}"

TAG=$region-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot

# Set the version tag. If the latest flag is used, use the latest tag
if [[ $latest == "true" ]]; then
  VERSION_TAG=latest
else
  VERSION_TAG=$(date +%Y%m%d%H%M)
fi

# `latest` is a shared tag that consumers pull by name (the GPU labeler job,
# `sky local up`), so it stays unsuffixed unless -s says otherwise.
if [[ $suffix_set == "false" && $latest == "false" ]]; then
  if [[ $effective_base =~ ubuntu[:-]?([0-9]{2})\.?([0-9]{2}) ]]; then
    suffix="ubuntu${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
  else
    echo "Warning: cannot tell the Ubuntu release from base image" \
         "'${effective_base}'; tagging without a suffix. Pass -s to set one."
  fi
fi

if [[ $latest == "true" && -n $base_image && $suffix_set == "false" ]]; then
  echo "Warning: -l with -b overwrites the shared 'latest' tag with a variant" \
       "build. Pass -s to give it its own tag."
fi

echo "Tag suffix: ${suffix:-<none>}"

if [[ -n $suffix ]]; then
  VERSION_TAG=${VERSION_TAG}-${suffix}
fi

# Add -gpu to the tag if the GPU image is being built
if [[ $gpu == "true" ]]; then
  TAG=$TAG-gpu:${VERSION_TAG}
else
  TAG=$TAG:${VERSION_TAG}
fi

echo "Building image: $TAG"

# Set up Docker buildx for multi-platform builds if it's not already set up
if ! docker buildx inspect mybuilder >/dev/null 2>&1; then
  echo "Setting up Docker buildx builder for multi-platform builds..."
  docker buildx create --name mybuilder --driver docker-container --bootstrap
  docker buildx use mybuilder
fi

# If push is used, build the image for both amd64 and arm64
if [[ $push == "true" ]]; then
  # Build for both architectures
  echo "Building and pushing image for amd64 and arm64: $TAG"
  docker buildx build --push "${BUILD_ARGS[@]}" --platform linux/amd64,linux/arm64 -t $TAG -f $DOCKERFILE ./sky
else
  # Load the right image depending on the architecture of the host machine (Apple Silicon or Intel)
  if [[ $(uname -m) == "arm64" ]]; then
    echo "Loading image for arm64 (Apple Silicon etc.): $TAG"
    docker buildx build --load "${BUILD_ARGS[@]}" --platform linux/arm64 -t $TAG -f $DOCKERFILE ./sky
  elif [[ $(uname -m) == "x86_64" ]]; then
    echo "Building for amd64 (Intel CPUs): $TAG"
    docker buildx build --load "${BUILD_ARGS[@]}" --platform linux/amd64 -t $TAG -f $DOCKERFILE ./sky
  else
    echo "Unsupported architecture: $(uname -m)"
    exit 1
  fi

  echo "Tagging image."
  if [[ "$gpu" == "true" ]]; then
    docker tag $TAG skypilot-gpu:latest
  else
    docker tag $TAG skypilot:latest
  fi
fi
