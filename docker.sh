#!/bin/bash

# This script is run inside the Cursor agent container
# It must be idempotent as Cursor will snapshot the container after running it

set -euxo pipefail

# Configure Docker-in-Docker (DinD) to work in the Cursor agent environment
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "iptables": false,
  "storage-driver": "vfs"
}
EOF

# Restart Docker to pick up new configuration
sudo service docker stop || true

# Cursor init doesn't properly reap zombie processes
sudo pkill -f dockerd || true
sudo rm -f /var/run/docker*.pid

# Start Docker
sudo service docker start

# For some reason the agent shell isn't picking up group membership
# Force docker command to run with the correct group
grep -q 'docker()' ~/.bashrc || echo 'docker() { sudo -g docker docker "$@"; }' >> ~/.bashrc
