#!/bin/bash

# Popular third-party dependencies
sudo apt-get install jq -y

# Function to find processes using a specific file
mylsof() {
  p=$(for pid in /proc/{0..9}*; do
    i=$(basename "$pid")
    for file in "$pid"/fd/*; do
      link=$(readlink -e "$file")
      if [ "$link" = "$1" ]; then
        echo "$i"
      fi
    done
  done)
  echo "$p"
}

# Stop and disable unattended-upgrades
sudo systemctl stop unattended-upgrades || true
sudo systemctl disable unattended-upgrades || true
sudo sed -i 's/Unattended-Upgrade "1"/Unattended-Upgrade "0"/g' /etc/apt/apt.conf.d/20auto-upgrades || true

# Release the dpkg lock if held
p=$(mylsof "/var/lib/dpkg/lock-frontend")
echo "$p"
sudo kill -9 $(echo "$p" | tail -n 1) || true
sudo rm /var/lib/dpkg/lock-frontend
sudo pkill -9 dpkg
sudo pkill -9 apt-get

# Configure dpkg
sudo dpkg --configure --force-overwrite -a

# Create necessary directories
mkdir -p ~/sky_workdir
mkdir -p ~/.sky/
mkdir -p ~/.sky/sky_app

# Configure SSH
mkdir -p ~/.ssh
touch ~/.ssh/config

# Install Miniconda
curl -o Miniconda3-Linux-x86_64.sh https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-x86_64.sh
bash Miniconda3-Linux-x86_64.sh -b
eval "$(~/miniconda3/bin/conda shell.bash hook)"
conda init
conda config --set auto_activate_base true
conda activate base

# Ensure conda is initialized in ~/.bashrc
grep "# >>> conda initialize >>>" ~/.bashrc || { conda init && source ~/.bashrc; }

# Create conda environment with Python 3.10
echo "Creating conda env with Python 3.10"
conda create -y -n skypilot-runtime python=3.10
conda activate skypilot-runtime

# Set up Python environment
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m venv ~/skypilot-runtime --system-site-packages
echo "$(echo ~/skypilot-runtime)/bin/python" > ~/.sky/python_path
source ~/.bashrc

# Configure Python and PIP
export PIP_DISABLE_PIP_VERSION_CHECK=1
echo PATH=$PATH
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m pip install "setuptools<70"

# Install ray
RAY_ADDRESS=127.0.0.1:6380
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) $([ -s ~/.sky/ray_path ] && cat ~/.sky/ray_path 2> /dev/null || which ray) status
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m pip install --exists-action w -U ray[default]==2.9.3
export PATH=$PATH:$HOME/.local/bin
source ~/skypilot-runtime/bin/activate
which ray > ~/.sky/ray_path || exit 1

# Install SkyPilot nightly
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m pip install "skypilot-nightly"

# Patch Ray if installed
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m pip list | grep "ray " | grep 2.9.3 2>&1 > /dev/null && {
  $([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -c "from sky.skylet.ray_patches import patch; patch()" || exit 1
}

# System configurations
sudo bash -c 'rm -rf /etc/security/limits.d; echo "* soft nofile 1048576" >> /etc/security/limits.conf; echo "* hard nofile 1048576" >> /etc/security/limits.conf'
sudo grep -e '^DefaultTasksMax' /etc/systemd/system.conf || sudo bash -c 'echo "DefaultTasksMax=infinity" >> /etc/systemd/system.conf'
sudo systemctl set-property user-$(id -u $(whoami)).slice TasksMax=infinity
sudo systemctl daemon-reload

# Stop and disable Jupyter service
sudo systemctl stop jupyter > /dev/null 2>&1 || true
sudo systemctl disable jupyter > /dev/null 2>&1 || true

# Configure fuse
[ -f /etc/fuse.conf ] && sudo sed -i 's/#user_allow_other/user_allow_other/g' /etc/fuse.conf || sudo sh -c 'echo "user_allow_other" > /etc/fuse.conf'

# Remove SkyPilot in OS image because when user sky launch we will install whatever version of SkyPilot user has on their local machine.
$([ -s ~/.sky/python_path ] && cat ~/.sky/python_path 2> /dev/null || which python3) -m pip uninstall "skypilot-nightly" -y