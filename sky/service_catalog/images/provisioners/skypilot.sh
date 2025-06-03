#!/bin/bash

# Stop and disable unattended-upgrades
sudo systemctl stop unattended-upgrades || true
sudo systemctl disable unattended-upgrades || true
sudo sed -i 's/Unattended-Upgrade "1"/Unattended-Upgrade "0"/g' /etc/apt/apt.conf.d/20auto-upgrades || true
sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
sudo systemctl disable apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
sudo systemctl mask apt-daily.service apt-daily-upgrade.service unattended-upgrades.service
sudo systemctl daemon-reload

# Configure dpkg
sudo dpkg --configure --force-overwrite -a

# Apt-get installs
sudo apt-get install jq -y
sudo apt install retry

# Create necessary directories
mkdir -p ~/sky_workdir
mkdir -p ~/.sky/
mkdir -p ~/.sky/sky_app
mkdir -p ~/.ssh
touch ~/.ssh/config

# Install Miniconda
curl -o Miniconda3-Linux-x86_64.sh https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-x86_64.sh
bash Miniconda3-Linux-x86_64.sh -b
eval "$(~/miniconda3/bin/conda shell.bash hook)"
rm Miniconda3-Linux-x86_64.sh
conda init
conda config --set auto_activate_base true
conda activate base

# Conda, Python
echo "Creating conda env with Python 3.10"
conda create -y -n skypilot-runtime python=3.10
conda activate skypilot-runtime
export PIP_DISABLE_PIP_VERSION_CHECK=1
echo PATH=$PATH
python3 -m venv ~/skypilot-runtime
PYTHON_EXEC=$(echo ~/skypilot-runtime)/bin/python

# Install SkyPilot
$PYTHON_EXEC -m pip install "skypilot-nightly[remote]"

# Install Ray
RAY_ADDRESS=127.0.0.1:6380
$PYTHON_EXEC -m pip install --exists-action w -U "ray[default]==2.9.3"
export PATH=$PATH:$HOME/.local/bin
source ~/skypilot-runtime/bin/activate
which ray > ~/.sky/ray_path || exit 1
$PYTHON_EXEC -m pip list | grep "ray " | grep 2.9.3 2>&1 > /dev/null && {
  $PYTHON_EXEC -c "from sky.skylet.ray_patches import patch; patch()" || exit 1
}

# Install cloud dependencies
if [ "$CLOUD" = "azure" ]; then
    $PYTHON_EXEC -m pip install "skypilot-nightly[azure]"
elif [ "$CLOUD" = "gcp" ]; then
    # We don't have to install the google-cloud-sdk since it is installed by default in GCP machines.
    $PYTHON_EXEC -m pip install "skypilot-nightly[gcp]"
elif [ "$CLOUD" = "aws" ]; then
    $PYTHON_EXEC -m pip install "skypilot-nightly[aws]"
else
    echo "Error: Unknown cloud $CLOUD so not installing any cloud dependencies."
fi

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

# Cleanup
# Remove SkyPilot in OS image because when user sky launch we will install whatever version of SkyPilot user has on their local machine.
$PYTHON_EXEC -m pip uninstall "skypilot-nightly" -y
rm -rf ~/.sky
