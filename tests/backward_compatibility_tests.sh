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
#   bash tests/backward_compatibility_tests.sh [need_launch] [start_from] [base_branch]
#
#   need_launch: Whether to launch a new cluster after switching to the current environment. 0 or 1. Default: 0.
#   start_from:  The section number to start the test from. Integer from 1 to 8. Default: 0.
#   base_branch: The base branch to clone skypilot from. Default: master.
#
# Example Usage:
#   1. Run all tests with default settings (test managed jobs, starting from section 7, using 'master' as the base branch):
#      bash tests/backward_compatibility_tests.sh
#
#   2. Run all tests, launch a new cluster after upgrading, and use the 'dev' branch as the base:
#      bash tests/backward_compatibility_tests.sh 1 1 dev
#
#   3. Start from section 3 (sky autostop + sky status -r), using the default base branch ('master'):
#      bash tests/backward_compatibility_tests.sh 0 3
#
#   4.  Start from section 5, do not launch, and specify base branch as my-branch
#       bash tests/backward_compatibility_tests.sh 0 5 my-branch

#!/bin/bash
set -evx

# Store the initial directory
ABSOLUTE_CURRENT_VERSION_DIR=$(pwd)
BASE_VERSION_DIR="../sky-base"
ABSOLUTE_BASE_VERSION_DIR=$(cd "$(dirname "$BASE_VERSION_DIR")" 2>/dev/null && pwd || pwd)/$(basename "$BASE_VERSION_DIR")
rm -rf $ABSOLUTE_BASE_VERSION_DIR

need_launch=${1:-0}
start_from=${2:-7}
base_branch=${3:-master}

CLUSTER_NAME="test-back-compat-$(whoami)"
# Test managed jobs to make sure existing jobs and new job can run correctly,
# after the jobs controller is updated.
# Get a new uuid to avoid conflict with previous back-compat tests.
uuid=$(uuidgen)
MANAGED_JOB_JOB_NAME=${CLUSTER_NAME}-${uuid:0:4}
CLOUD="aws"

# Function to activate a given environment
activate_env() {
  local env_name="$1"
  local restart_api_server="${2:-1}"
  local env_dir
  local absolute_dir

  # Determine environment directory and absolute path based on env_name
  case "$env_name" in
    base)
      env_dir="sky-back-compat-base"
      absolute_dir="$ABSOLUTE_BASE_VERSION_DIR"
      ;;
    current)
      env_dir="sky-back-compat-current"
      absolute_dir="$ABSOLUTE_CURRENT_VERSION_DIR"
      ;;
    *)
      echo "Invalid environment name: '$env_name'. Must be 'base' or 'current'."
      exit 1
      ;;
  esac

  # Argument validation
  if [ "$#" -gt 2 ]; then
    echo "Usage: activate_env <environment (base or current)> [<restart_api_server (0 or 1)>]"
    exit 1
  fi
  if ! [[ "$restart_api_server" =~ ^[01]$ ]]; then
    echo "Invalid restart_api_server value: '$restart_api_server'. Must be 0 or 1."
    exit 1
  fi

  rm -r  ~/.sky/wheels || true
  # Activate the environment
  set +vx  # Temporarily disable verbose tracing
  source ~/"$env_dir"/bin/activate
  set -vx  # Re-enable verbose tracing

  # Show which sky is being used.
  which sky

  # Change to the environment directory
  cd "$absolute_dir" || {
    echo "Failed to change directory to $absolute_dir"
    exit 1
  }

  # API operations: Restart the API if restart_api_server is 1
  if [ "$restart_api_server" -eq 1 ]; then
    sky api stop || true  # Stop, ignoring errors if it's not running
    if ! sky api start; then
      echo "Failed to start sky API"
      exit 1
    fi
  fi
}

deactivate_env() {
  set +vx  # Temporarily disable verbose tracing
  sky api stop || true  # Stop, ignoring errors if it's not running
  deactivate
  set -vx  # Re-enable verbose tracing
}

git clone -b ${base_branch} https://github.com/skypilot-org/skypilot.git $ABSOLUTE_BASE_VERSION_DIR
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

activate_env "base" 0
$UV pip uninstall skypilot
$UV pip install --prerelease=allow azure-cli
$UV pip install -e ".[all]"
deactivate_env

activate_env "current" 0
$UV pip uninstall skypilot
$UV pip install --prerelease=allow azure-cli
$UV pip install -e ".[all]"
deactivate_env

cleanup_resources() {
  activate_env "current"
  sky down ${CLUSTER_NAME}* -y || true
  sky jobs cancel -n ${MANAGED_JOB_JOB_NAME}* -y || true
  sky serve down ${CLUSTER_NAME}-* -y || true
  sky api stop
  deactivate_env
}

# Set trap to call cleanup on script exit
trap cleanup_resources EXIT

# exec + launch
if [ "$start_from" -le 1 ]; then
activate_env "base"
# Job 1
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME} examples/minimal.yaml
sky autostop -i 10 -y ${CLUSTER_NAME}
# Job 2
sky exec -d --cloud ${CLOUD} --num-nodes 2 ${CLUSTER_NAME} sleep 100
deactivate_env

activate_env "current"
# The original cluster should exist in the status output
sky status ${CLUSTER_NAME} | grep ${CLUSTER_NAME} | grep UP
sky status -r ${CLUSTER_NAME} | grep ${CLUSTER_NAME} | grep UP
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
deactivate_env
fi

# sky stop + sky start + sky exec
if [ "$start_from" -le 2 ]; then
activate_env "base"
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-2 examples/minimal.yaml
deactivate_env
activate_env "current"
sky stop -y ${CLUSTER_NAME}-2
sky start -y ${CLUSTER_NAME}-2
s=$(sky exec --cloud ${CLOUD} -d ${CLUSTER_NAME}-2 examples/minimal.yaml)
echo "$s"
echo "$s" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job submitted, ID: 2" || exit 1
deactivate_env
fi

# `sky autostop` + `sky status -r`
if [ "$start_from" -le 3 ]; then
activate_env "base"
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-3 examples/minimal.yaml
deactivate_env
activate_env "current"
sky autostop -y -i0 ${CLUSTER_NAME}-3
sleep 120
sky status -r ${CLUSTER_NAME}-3 | grep STOPPED || exit 1
deactivate_env
fi


# (1 node) sky launch --cloud ${CLOUD} + sky exec + sky queue + sky logs
if [ "$start_from" -le 4 ]; then
activate_env "base"
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-4
deactivate_env
activate_env "current"
sky launch --cloud ${CLOUD} -y --num-nodes 2 -c ${CLUSTER_NAME}-4 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-4
sky logs ${CLUSTER_NAME}-4 1 --status
sky logs ${CLUSTER_NAME}-4 2 --status
sky logs ${CLUSTER_NAME}-4 1
sky logs ${CLUSTER_NAME}-4 2
deactivate_env
fi

# (1 node) sky start + sky exec + sky queue + sky logs
if [ "$start_from" -le 5 ]; then
activate_env "base"
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky stop -y ${CLUSTER_NAME}-5
deactivate_env
activate_env "current"
sky start -y ${CLUSTER_NAME}-5
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 1 --status
sky logs ${CLUSTER_NAME}-5 1
sky launch --cloud ${CLOUD} -y -c ${CLUSTER_NAME}-5 examples/minimal.yaml
sky queue ${CLUSTER_NAME}-5
sky logs ${CLUSTER_NAME}-5 2 --status
sky logs ${CLUSTER_NAME}-5 2
deactivate_env
fi

# (2 nodes) sky launch --cloud ${CLOUD} + sky exec + sky queue + sky logs
if [ "$start_from" -le 6 ]; then
activate_env "base"
sky launch --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -c ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky stop -y ${CLUSTER_NAME}-6
deactivate_env
activate_env "current"
sky start -y ${CLUSTER_NAME}-6
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 1 --status
sky logs ${CLUSTER_NAME}-6 1
sky exec --cloud ${CLOUD} ${CLUSTER_NAME}-6 examples/multi_hostname.yaml
sky queue ${CLUSTER_NAME}-6
sky logs ${CLUSTER_NAME}-6 2 --status
sky logs ${CLUSTER_NAME}-6 2
deactivate_env
fi

if [ "$start_from" -le 7 ]; then
activate_env "base"
sky jobs launch -d --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -n ${MANAGED_JOB_JOB_NAME}-7-0 "echo hi; sleep 1000"
sky jobs launch -d --cloud ${CLOUD} -y --cpus 2 --num-nodes 2 -n ${MANAGED_JOB_JOB_NAME}-7-1 "echo hi; sleep 400"
deactivate_env
activate_env "current"
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
deactivate_env
fi

# sky serve
if [ "$start_from" -le 8 ]; then
activate_env "base"
sky serve up --cloud ${CLOUD} -y --cpus 2 -y -n ${CLUSTER_NAME}-8-0 examples/serve/http_server/task.yaml
sky serve status ${CLUSTER_NAME}-8-0
deactivate_env

activate_env "current"
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
deactivate_env
fi
