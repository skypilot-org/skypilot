#!/bin/bash

PYTHON_EXEC=$(echo ~/skypilot-runtime)/bin/python

# TODO: keep this dependency installation align with utils/controller_utils.py and setup.py
install_azure() {
    echo "Install cloud dependencies on controller: Azure"
    $PYTHON_EXEC -m pip install "azure-cli>=2.31.0" azure-core "azure-identity>=1.13.0" azure-mgmt-network
    $PYTHON_EXEC -m pip install azure-storage-blob msgraph-sdk
}

install_gcp() {
    echo "Install cloud dependencies on controller: GCP"
    $PYTHON_EXEC -m pip install "google-api-python-client>=2.69.0"
    $PYTHON_EXEC -m pip install google-cloud-storage
    conda install -n skypilot-runtime -c conda-forge google-cloud-sdk
}

install_aws() {
    echo "Install cloud dependencies on controller: AWS"
    $PYTHON_EXEC -m pip install botocore>=1.29.10 boto3>=1.26.1
    $PYTHON_EXEC -m pip install "urllib3<2" awscli>=1.27.10 "colorama<0.4.5"
}

if [ "$CLOUD" = "azure" ]; then
    install_azure
elif [ "$CLOUD" = "gcp" ]; then
    install_gcp
elif [ "$CLOUD" = "aws" ]; then
    install_aws
else
    echo "Error: Unknown cloud $CLOUD so not installing any cloud dependencies."
fi

if [ $? -eq 0 ]; then
    echo "Successfully installed cloud dependencies on controller: $CLOUD"
else
    echo "Error: Failed to install cloud dependencies on controller: $CLOUD"
fi
