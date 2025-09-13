#!/bin/bash

set -e

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg &&
	curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list |
	sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' |
		sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# if there's an empty /etc/docker/daemon.json, `nvidia-ctk runtime configure --runtime=docker` will fail
if [ -f /etc/docker/daemon.json ] && [ ! -s /etc/docker/daemon.json ]; then
	sudo rm /etc/docker/daemon.json
fi

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Validate
if sudo docker info -f "{{.Runtimes}}" | grep "nvidia-container-runtime"; then
    echo "Successfully installed NVIDIA container runtime"
else
	echo "Failed to install NVIDIA container runtime"
fi
