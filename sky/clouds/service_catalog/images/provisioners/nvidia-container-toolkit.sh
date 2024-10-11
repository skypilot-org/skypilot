#!/bin/bash

set -e

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
	| sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
	| sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
	| sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker