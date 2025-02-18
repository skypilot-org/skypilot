# Script for testing backward compatibility of skypilot.
#
# To run this script, you need to uninstall the skypilot and ray in the base
# conda environment, and run it in the base conda environment.
#
# It's recommended to use a smoke-test VM to run this.
#
# Usage:
#
#   cd skypilot-repo
#   git checkout <feature branch>
#   pip uninstall -y skypilot ray
#   bash tests/backward_compatibility_tests.sh

#!/bin/bash
set -evx

# Store the initial directory
ABSOLUTE_CURRENT_VERSION_DIR=$(pwd)
BASE_VERSION_DIR="../sky-base"
ABSOLUTE_BASE_VERSION_DIR=$(cd "$(dirname "$BASE_VERSION_DIR")" 2>/dev/null && pwd || pwd)/$(basename "$BASE_VERSION_DIR")

need_launch=${1:-0}
start_from=${2:-0}
base_branch=${3:-master}

CLUSTER_NAME="test-back-compat-$(whoami)"
# Test managed jobs to make sure existing jobs and new job can run correctly,
# after the jobs controller is updated.
# Get a new uuid to avoid conflict with previous back-compat tests.
uuid=$(uuidgen)
MANAGED_JOB_JOB_NAME=${CLUSTER_NAME}-${uuid:0:4}
CLOUD="aws"

# Function to activate the base environment and change directory
activate_base_env() {
  # Activate the base environment
  source ~/sky-back-compat-base/bin/activate

  # Change to the sky-base directory
  cd "$ABSOLUTE_BASE_VERSION_DIR" || {
    echo "Failed to change directory to $ABSOLUTE_BASE_VERSION_DIR"
    exit 1
  }

  # Check if the 'sky' command exists
  if command -v sky &> /dev/null; then
    # Stop the sky API if it is running, ignore errors if it's not
    sky api stop || true

    # Start the sky API
    if ! sky api start; then
      echo "Failed to start sky API"
      exit 1
    fi
  else
    echo "'sky' command not found. Skipping sky API operations."
  fi
}

# Function to activate the current environment and return to the initial directory
activate_current_env() {
  # Activate the current environment
  source ~/sky-back-compat-current/bin/activate

  # Change to the current directory
  cd "$ABSOLUTE_CURRENT_VERSION_DIR" || {
    echo "Failed to change directory to $ABSOLUTE_CURRENT_VERSION_DIR"
    exit 1
  }

  # Check if the 'sky' command exists
  if command -v sky &> /dev/null; then
    # Stop the sky API if it is running, ignore errors if it's not
    sky api stop || true

    # Start the sky API
    if ! sky api start; then
      echo "Failed to start sky API"
      exit 1
    fi
  else
    echo "'sky' command not found. Skipping sky API operations."
  fi
}

git clone -b ${base_branch} https://github.com/skypilot-org/skypilot.git $ABSOLUTE_BASE_VERSION_DIR || true
~/.local/bin/uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh
UV=~/.local/bin/uv

# Create environment for compatibility tests
source ~/.bashrc
$UV venv --seed --python=3.9 ~/sky-back-compat-base
$UV venv --seed --python=3.9 ~/sky-back-compat-current

# Install gcloud
if ! gcloud --version; then
  wget --quiet https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-424.0.0-linux-x86_64.tar.gz
  tar xzf google-cloud-sdk-424.0.0-linux-x86_64.tar.gz
  rm -rf ~/google-cloud-sdk
  mv google-cloud-sdk ~/
  ~/google-cloud-sdk/install.sh -q
  echo "source ~/google-cloud-sdk/path.bash.inc > /dev/null 2>&1" >> ~/.bashrc
  source ~/google-cloud-sdk/path.bash.inc
fi
rm -r  ~/.sky/wheels || true

activate_base_env
git pull origin ${base_branch}
$UV pip uninstall skypilot
$UV pip install --prerelease=allow azure-cli
$UV pip install -e ".[all]"
deactivate

activate_current_env
rm -r  ~/.sky/wheels || true
$UV pip uninstall skypilot
$UV pip install --prerelease=allow azure-cli
$UV pip install -e ".[all]"
deactivate

cleanup_resources() {
  activate_current_env
  sky down ${CLUSTER_NAME}* -y || true
  sky jobs cancel -n ${MANAGED_JOB_JOB_NAME}* -y || true
  sky serve down ${CLUSTER_NAME}-* -y || true
  sky api stop
  deactivate
}

# Set trap to call cleanup on script exit
trap cleanup_resources EXIT

# exec + launch
if [ "$start_from" -le 1 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
which sky
# Job 1
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME} examples/minimal.yaml
sky autostop -i 10 -y ${CLUSTER_NAME}
# Job 2
sky exec -d --cloud ${CLOUD} --num-nodes 2 ${CLUSTER_NAME} sleep 100
deactivate

activate_current_env
# The original cluster should exist in the status output
sky status ${CLUSTER_NAME} | grep ${CLUSTER_NAME} | grep UP
sky status -r ${CLUSTER_NAME} | grep ${CLUSTER_NAME} | grep UP
rm -r  ~/.sky/wheels || true
if [ "$need_launch" -eq "1" ]; then
  sky launch --cloud ${CLOUD} -y -c ${CLUSTER_NAME}
fi
# Job 3
sky exec -d --cloud ${CLOUD} ${CLUSTER_NAME} sleep 50
q=$(sky queue -u ${CLUSTER_NAME})
echo "$q"
echo "$q" | grep "RUNNING" | wc -l | grep 2 || exit 1
# Job 4
s=$(sky launch --cloud ${CLOUD} -d -c ${CLUSTER_NAME} examples/minimal.yaml)
sky logs ${CLUSTER_NAME} 2 --status | grep "RUNNING\|SUCCEEDED" || exit 1
# remove color and find the job id
echo "$s" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job ID: 4" || exit 1
# wait for ready
sky logs ${CLUSTER_NAME} 2
q=$(sky queue -u ${CLUSTER_NAME})
echo "$q"
echo "$q" | grep "SUCCEEDED" | wc -l | grep 4 || exit 1
deactivate
fi

# sky stop + sky start + sky exec
if [ "$start_from" -le 2 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-2 examples/minimal.yaml
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
sky stop -y ${CLUSTER_NAME}-2
sky start -y ${CLUSTER_NAME}-2
s=$(sky exec --cloud ${CLOUD} -d ${CLUSTER_NAME}-2 examples/minimal.yaml)
echo "$s"
echo "$s" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job submitted, ID: 2" || exit 1
deactivate
fi

# `sky autostop` + `sky status -r`
if [ "$start_from" -le 3 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-3 examples/minimal.yaml
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
sky autostop -y -i0 ${CLUSTER_NAME}-3
sleep 120
sky status -r ${CLUSTER_NAME}-3 | grep STOPPED || exit 1
deactivate
fi


# (1 node) sky launch --cloud ${CLOUD} + sky exec + sky queue + sky logs
if [ "$start_from" -le 4 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-4
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --num-nodes 2 -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-4
sky logs ${CLUSTER_NAME}-4 1 --status
sky logs ${CLUSTER_NAME}-4 2 --status
sky logs ${CLUSTER_NAME}-4 1
sky logs ${CLUSTER_NAME}-4 2
deactivate
fi

# (1 node) sky start + sky exec + sky queue + sky logs
if [ "$start_from" -le 5 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-5
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
sky start -y ${CLUSTER_NAME}-5
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 1 --status
sky logs ${CLUSTER_NAME}-5 1
sky launch --cloud ${CLOUD} -y -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 2 --status
sky logs ${CLUSTER_NAME}-5 2
deactivate
fi

# (2 nodes) sky launch --cloud ${CLOUD} + sky exec + sky queue + sky logs
if [ "$start_from" -le 6 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky stop -y ${CLUSTER_NAME}-6
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
sky start -y ${CLUSTER_NAME}-6
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 1 --status
sky logs ${CLUSTER_NAME}-6 1
sky exec --cloud ${CLOUD} ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 2 --status
sky logs ${CLUSTER_NAME}-6 2
deactivate
fi

if [ "$start_from" -le 7 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky jobs launch -d --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -n ${MANAGED_JOB_JOB_NAME}-7-0 "echo hi; sleep 1000"
sky jobs launch -d --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -n ${MANAGED_JOB_JOB_NAME}-7-1 "echo hi; sleep 400"
deactivate
activate_current_env
rm -r  ~/.sky/wheels || true
s=$(sky jobs queue | grep ${MANAGED_JOB_JOB_NAME}-7 | grep "RUNNING" | wc -l)
s=$(sky jobs logs --no-follow -n ${MANAGED_JOB_JOB_NAME}-7-1)
echo "$s"
echo "$s" | grep " hi" || exit 1
sky jobs launch -d --cloud ${CLOUD} --num-nodes 2 -y -n ${MANAGED_JOB_JOB_NAME}-7-2 "echo hi; sleep 40"
s=$(sky jobs logs --no-follow -n ${MANAGED_JOB_JOB_NAME}-7-2)
echo "$s"
echo "$s" | grep " hi" || exit 1
s=$(sky jobs queue | grep ${MANAGED_JOB_JOB_NAME}-7)
echo "$s"
echo "$s" | grep "RUNNING" | wc -l | grep 3 || exit 1
sky jobs cancel -y -n ${MANAGED_JOB_JOB_NAME}-7-0
sky jobs logs -n "${MANAGED_JOB_JOB_NAME}-7-1" || exit 1
s=$(sky jobs queue | grep ${MANAGED_JOB_JOB_NAME}-7)
echo "$s"
echo "$s" | grep "SUCCEEDED" | wc -l | grep 2 || exit 1
echo "$s" | grep "CANCELLING\|CANCELLED" | wc -l | grep 1 || exit 1
deactivate
fi

# sky serve
if [ "$start_from" -le 8 ]; then
activate_base_env
rm -r  ~/.sky/wheels || true
sky serve up --cloud ${CLOUD} -y --cpus 2 -y -n ${CLUSTER_NAME}-8-0 examples/serve/http_server/task.yaml
sky serve status ${CLUSTER_NAME}-8-0
deactivate

activate_current_env
rm -r  ~/.sky/wheels || true
sky serve status
sky serve logs ${CLUSTER_NAME}-8-0 2 --no-follow
sky serve logs --controller ${CLUSTER_NAME}-8-0  --no-follow
sky serve logs --load-balancer ${CLUSTER_NAME}-8-0  --no-follow
sky serve update ${CLUSTER_NAME}-8-0 -y --cloud ${CLOUD} --cpus 2 --num-nodes 4 examples/serve/http_server/task.yaml
sky serve logs --controller ${CLUSTER_NAME}-8-0  --no-follow
sky serve up --cloud ${CLOUD} -y --cpus 2 -n ${CLUSTER_NAME}-8-1 examples/serve/http_server/task.yaml
sky serve status ${CLUSTER_NAME}-8-1
sky serve down ${CLUSTER_NAME}-8-0 -y
sky serve logs --controller ${CLUSTER_NAME}-8-1  --no-follow
sky serve logs --load-balancer ${CLUSTER_NAME}-8-1  --no-follow
sky serve down ${CLUSTER_NAME}-8-1 -y
deactivate
fi
