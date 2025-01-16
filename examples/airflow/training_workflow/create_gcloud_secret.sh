#!/bin/bash

# Define variables
GCLOUD_DIR="$HOME/.config/gcloud"
TAR_FILE="gcloud-config.tar.gz"
SECRET_NAME="gcloud-secret"

# List of files and directories to include in the tarball
FILES_TO_TAR=(
  "credentials.db"
  "access_tokens.db"
  "configurations"
  "legacy_credentials"
  "active_config"
  "application_default_credentials.json"
)

# Create a tarball with the specified files and directories
echo "Creating tarball..."
tar -czvf $TAR_FILE -C $GCLOUD_DIR "${FILES_TO_TAR[@]}"

# Create the Kubernetes Secret using the tarball
echo "Creating Kubernetes secret..."
kubectl create secret generic $SECRET_NAME --from-file=gcloud-config.tar.gz=$TAR_FILE

# Remove the tarball after the secret is created
echo "Cleaning up tarball..."
rm -f $TAR_FILE

echo "Secret '$SECRET_NAME' created successfully and temporary tarball removed."
