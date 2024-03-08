#!/bin/bash
# Builds and pushes the Dockerfile_ha image with the specified tag.
# Usage: ./build_ha_image.sh IMAGE_TAG
# Example: ./build_ha_image.sh berkeleyskypilot/skypilot-ha-lb:latest

TAG=$1

if [[ -z $TAG ]]; then
  echo "Usage: ./build_ha_image.sh IMAGE_TAG"
  exit 1
fi

echo "Building image: $TAG"

# Navigate to the root of the project (inferred from git)
cd "$(git rev-parse --show-toplevel)"

docker buildx build --push --platform linux/amd64 -t $TAG -f Dockerfile_ha .
