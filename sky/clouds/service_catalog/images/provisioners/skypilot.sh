#!/bin/bash

# Stop and disable unattended-upgrades
sudo systemctl stop unattended-upgrades || true
sudo systemctl disable unattended-upgrades || true
sudo sed -i 's/Unattended-Upgrade "1"/Unattended-Upgrade "0"/g' /etc/apt/apt.conf.d/20auto-upgrades || true

# Configure dpkg
sudo dpkg --configure --force-overwrite -a

# Apt-get installs
sudo apt-get install jq -y

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

# Pip installs
$PYTHON_EXEC -m pip install "setuptools<70" "grpcio!=1.48.0,<=1.51.3,>=1.42.0" "platformdirs<5,>=3.9.1" "rpds-py>=0.7.1" "referencing>=0.28.4" "jsonschema-specifications>=2023.03.6" "six~=1.16" "google-api-core<3.0.0,>=1.0.0" "opencensus-context>=0.1.3" "charset-normalizer<4,>=2" "urllib3<3,>=1.21.1" "idna<4,>=2.5" "certifi>=2017.4.17" "wrapt" "wcwidth>=0.1.4" "google-auth<3.0.dev0,>=2.14.1" "proto-plus<2.0.0dev,>=1.22.3" "googleapis-common-protos<2.0.dev0,>=1.56.2" "propcache>=0.2.0" "pyasn1-modules>=0.2.1" "cachetools<6.0,>=2.0.0" "rsa<5,>=3.1.4" "pyasn1<0.7.0,>=0.4.6" "networkx" "wheel" "pendulum" "cryptography" "pandas>=1.3.0" "PrettyTable>=2.0.0" "colorama" "jinja2>=3.0" "tabulate" "python-dotenv" "rich" "pulp" "kubernetes>=20.0.0" "grpcio!=1.48.0,<=1.51.3,>=1.42.0" "MarkupSafe>=2.0" "python-dateutil>=2.5.3" "oauthlib>=3.2.2" "durationpy>=0.7" "websocket-client!=0.40.0,!=0.41.*,!=0.42.*,>=0.32.0" "requests-oauthlib" "tzdata>=2022.7" "numpy>=1.22.4" "pytz>=2020.1" "cffi>=1.12" "time-machine>=2.6.0" "markdown-it-py>=2.2.0" "pygments<3.0.0,>=2.13.0" "pycparser" "mdurl~=0.1" "yarl<2.0,>=1.12.0" "async-timeout<5.0,>=4.0" "nvidia-ml-py>=11.450.129" "blessed>=1.17.1" "annotated-types>=0.6.0" "pydantic-core==2.23.4" "typing-extensions>=4.6.1" "distlib<1,>=0.3.7"
$PYTHON_EXEC -m pip install "skypilot-nightly"

# Install ray
RAY_ADDRESS=127.0.0.1:6380
$PYTHON_EXEC -m pip install --exists-action w -U "ray[default]==2.9.3"
export PATH=$PATH:$HOME/.local/bin
source ~/skypilot-runtime/bin/activate
which ray > ~/.sky/ray_path || exit 1
$PYTHON_EXEC -m pip list | grep "ray " | grep 2.9.3 2>&1 > /dev/null && {
  $PYTHON_EXEC -c "from sky.skylet.ray_patches import patch; patch()" || exit 1
}

ls ~/skypilot-runtime/bin/python
# Install cloud dependencies
if [ "$CLOUD" = "azure" ]; then
    $PYTHON_EXEC -m pip install "skypilot-nightly[azure]"
elif [ "$CLOUD" = "gcp" ]; then
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
