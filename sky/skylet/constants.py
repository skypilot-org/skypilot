"""Constants for SkyPilot."""
from typing import List, Tuple

from packaging import version

import sky

SKY_LOGS_DIRECTORY = '~/sky_logs'
SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_IGNORE_FILE = '.skyignore'
GIT_IGNORE_FILE = '.gitignore'

# Default Ray port is 6379. Default Ray dashboard port is 8265.
# Default Ray tempdir is /tmp/ray.
# We change them to avoid conflicts with user's Ray clusters.
# We note down the ports in ~/.sky/ray_port.json for backward compatibility.
SKY_REMOTE_RAY_PORT = 6380
SKY_REMOTE_RAY_DASHBOARD_PORT = 8266
# Note we can not use json.dumps which will add a space between ":" and its
# value which causes the yaml parser to fail.
SKY_REMOTE_RAY_PORT_DICT_STR = (
    f'{{"ray_port":{SKY_REMOTE_RAY_PORT}, '
    f'"ray_dashboard_port":{SKY_REMOTE_RAY_DASHBOARD_PORT}}}')
# The file contains the ports of the Ray cluster that SkyPilot launched,
# i.e. the PORT_DICT_STR above.
SKY_REMOTE_RAY_PORT_FILE = '~/.sky/ray_port.json'
SKY_REMOTE_RAY_TEMPDIR = '/tmp/ray_skypilot'
SKY_REMOTE_RAY_VERSION = '2.9.3'

# We store the absolute path of the python executable (/opt/conda/bin/python3)
# in this file, so that any future internal commands that need to use python
# can use this path. This is useful for the case where the user has a custom
# conda environment as a default environment, which is not the same as the one
# used for installing SkyPilot runtime (ray and skypilot).
SKY_PYTHON_PATH_FILE = '~/.sky/python_path'
SKY_RAY_PATH_FILE = '~/.sky/ray_path'
SKY_GET_PYTHON_PATH_CMD = (f'[ -s {SKY_PYTHON_PATH_FILE} ] && '
                           f'cat {SKY_PYTHON_PATH_FILE} 2> /dev/null || '
                           'which python3')
# Python executable, e.g., /opt/conda/bin/python3
SKY_PYTHON_CMD = f'$({SKY_GET_PYTHON_PATH_CMD})'
SKY_PIP_CMD = f'{SKY_PYTHON_CMD} -m pip'
# Ray executable, e.g., /opt/conda/bin/ray
# We need to add SKY_PYTHON_CMD before ray executable because:
# The ray executable is a python script with a header like:
#   #!/opt/conda/bin/python3
# When we create the skypilot-runtime venv, the previously installed ray
# executable will be reused (due to --system-site-packages), and that will cause
# running ray CLI commands to use the wrong python executable.
SKY_RAY_CMD = (f'{SKY_PYTHON_CMD} $([ -s {SKY_RAY_PATH_FILE} ] && '
               f'cat {SKY_RAY_PATH_FILE} 2> /dev/null || which ray)')
# Separate env for SkyPilot runtime dependencies.
SKY_REMOTE_PYTHON_ENV_NAME = 'skypilot-runtime'
SKY_REMOTE_PYTHON_ENV = f'~/{SKY_REMOTE_PYTHON_ENV_NAME}'
ACTIVATE_SKY_REMOTE_PYTHON_ENV = f'source {SKY_REMOTE_PYTHON_ENV}/bin/activate'
# Deleting the SKY_REMOTE_PYTHON_ENV_NAME from the PATH to deactivate the
# environment. `deactivate` command does not work when conda is used.
DEACTIVATE_SKY_REMOTE_PYTHON_ENV = (
    'export PATH='
    f'$(echo $PATH | sed "s|$(echo ~)/{SKY_REMOTE_PYTHON_ENV_NAME}/bin:||")')

# The name for the environment variable that stores the unique ID of the
# current task. This will stay the same across multiple recoveries of the
# same managed task.
TASK_ID_ENV_VAR = 'SKYPILOT_TASK_ID'
# This environment variable stores a '\n'-separated list of task IDs that
# are within the same managed job (DAG). This can be used by the user to
# retrieve the task IDs of any tasks that are within the same managed job.
# This environment variable is pre-assigned before any task starts
# running within the same job, and will remain constant throughout the
# lifetime of the job.
TASK_ID_LIST_ENV_VAR = 'SKYPILOT_TASK_IDS'

# The version of skylet. MUST bump this version whenever we need the skylet to
# be restarted on existing clusters updated with the new version of SkyPilot,
# e.g., when we add new events to skylet, we fix a bug in skylet, or skylet
# needs to load the new version of SkyPilot code to handle the autostop when the
# cluster yaml is updated.
#
# TODO(zongheng,zhanghao): make the upgrading of skylet automatic?
SKYLET_VERSION = '8'
# The version of the lib files that skylet/jobs use. Whenever there is an API
# change for the job_lib or log_lib, we need to bump this version, so that the
# user can be notified to update their SkyPilot version on the remote cluster.
SKYLET_LIB_VERSION = 1
SKYLET_VERSION_FILE = '~/.sky/skylet_version'

# `sky jobs dashboard`-related
#
# Port on the remote jobs controller that the dashboard is running on.
SPOT_DASHBOARD_REMOTE_PORT = 5000

# Docker default options
DEFAULT_DOCKER_CONTAINER_NAME = 'sky_container'
DEFAULT_DOCKER_PORT = 10022
DOCKER_USERNAME_ENV_VAR = 'SKYPILOT_DOCKER_USERNAME'
DOCKER_PASSWORD_ENV_VAR = 'SKYPILOT_DOCKER_PASSWORD'
DOCKER_SERVER_ENV_VAR = 'SKYPILOT_DOCKER_SERVER'
DOCKER_LOGIN_ENV_VARS = {
    DOCKER_USERNAME_ENV_VAR,
    DOCKER_PASSWORD_ENV_VAR,
    DOCKER_SERVER_ENV_VAR,
}

# Commands for disable GPU ECC, which can improve the performance of the GPU
# for some workloads by 30%. This will only be applied when a user specify
# `nvidia_gpus.disable_ecc: true` in ~/.sky/config.yaml.
# Running this command will reboot the machine, introducing overhead for
# provisioning the machine.
# https://portal.nutanix.com/page/documents/kbs/details?targetId=kA00e000000LKjOCAW
DISABLE_GPU_ECC_COMMAND = (
    # Check if the GPU ECC is enabled. We use `sudo which` to check nvidia-smi
    # because in some environments, nvidia-smi is not in path for sudo and we
    # should skip disabling ECC in this case.
    'sudo which nvidia-smi && echo "Checking Nvidia ECC Mode" && '
    'out=$(nvidia-smi -q | grep "ECC Mode" -A2) && '
    'echo "$out" && echo "$out" | grep Current | grep Enabled && '
    'echo "Disabling Nvidia ECC" && '
    # Disable the GPU ECC.
    'sudo nvidia-smi -e 0 && '
    # Reboot the machine to apply the changes.
    '{ sudo reboot || echo "Failed to reboot. ECC mode may not be disabled"; } '
    '|| true; ')

# Install conda on the remote cluster if it is not already installed.
# We use conda with python 3.10 to be consistent across multiple clouds with
# best effort.
# https://github.com/ray-project/ray/issues/31606
# We use python 3.10 to be consistent with the python version of the
# AWS's Deep Learning AMI's default conda environment.
CONDA_INSTALLATION_COMMANDS = (
    'which conda > /dev/null 2>&1 || '
    '{ curl https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-x86_64.sh -o Miniconda3-Linux-x86_64.sh && '  # pylint: disable=line-too-long
    # We do not use && for installation of conda and the following init commands
    # because for some images, conda is already installed, but not initialized.
    # In this case, we need to initialize conda and set auto_activate_base to
    # true.
    '{ bash Miniconda3-Linux-x86_64.sh -b; '
    'eval "$(~/miniconda3/bin/conda shell.bash hook)" && conda init && '
    # Caller should replace {conda_auto_activate} with either true or false.
    'conda config --set auto_activate_base {conda_auto_activate} && '
    'conda activate base; }; }; '
    'grep "# >>> conda initialize >>>" ~/.bashrc || '
    '{ conda init && source ~/.bashrc; };'
    # If Python version is larger then equal to 3.12, create a new conda env
    # with Python 3.10.
    # We don't use a separate conda env for SkyPilot dependencies because it is
    # costly to create a new conda env, and venv should be a lightweight and
    # faster alternative when the python version satisfies the requirement.
    '[[ $(python3 --version | cut -d " " -f 2 | cut -d "." -f 2) -ge 12 ]] && '
    'echo "Creating conda env with Python 3.10" && '
    f'conda create -y -n {SKY_REMOTE_PYTHON_ENV_NAME} python=3.10 && '
    f'conda activate {SKY_REMOTE_PYTHON_ENV_NAME};'
    # Create a separate conda environment for SkyPilot dependencies.
    # We use --system-site-packages to reuse the system site packages to avoid
    # the overhead of installing the same packages in the new environment.
    f'[ -d {SKY_REMOTE_PYTHON_ENV} ] || '
    f'{SKY_PYTHON_CMD} -m venv {SKY_REMOTE_PYTHON_ENV} --system-site-packages;'
    f'echo "$(echo {SKY_REMOTE_PYTHON_ENV})/bin/python" > {SKY_PYTHON_PATH_FILE};'
)

_sky_version = str(version.parse(sky.__version__))
RAY_STATUS = f'RAY_ADDRESS=127.0.0.1:{SKY_REMOTE_RAY_PORT} {SKY_RAY_CMD} status'
# Install ray and skypilot on the remote cluster if they are not already
# installed. {var} will be replaced with the actual value in
# backend_utils.write_cluster_config.
RAY_SKYPILOT_INSTALLATION_COMMANDS = (
    'mkdir -p ~/sky_workdir && mkdir -p ~/.sky/sky_app;'
    # Disable the pip version check to avoid the warning message, which makes
    # the output hard to read.
    'export PIP_DISABLE_PIP_VERSION_CHECK=1;'
    # Print the PATH in provision.log to help debug PATH issues.
    'echo PATH=$PATH; '
    # Install setuptools<=69.5.1 to avoid the issue with the latest setuptools
    # causing the error:
    #   ImportError: cannot import name 'packaging' from 'pkg_resources'"
    f'{SKY_PIP_CMD} install "setuptools<70"; '
    # Backward compatibility for ray upgrade (#3248): do not upgrade ray if the
    # ray cluster is already running, to avoid the ray cluster being restarted.
    #
    # We do this guard to avoid any Ray client-server version mismatch.
    # Specifically: If existing ray cluster is an older version say 2.4, and we
    # pip install new version say 2.9 wheels here, then subsequent sky exec
    # (ray job submit) will have v2.9 vs. 2.4 mismatch, similarly this problem
    # exists for sky status -r (ray status).
    #
    # NOTE: RAY_STATUS will only work for the cluster with ray cluster on our
    # latest ray port 6380, but those existing cluster launched before #1790
    # that has ray cluster on the default port 6379 will be upgraded and
    # restarted.
    f'{SKY_PIP_CMD} list | grep "ray " | '
    f'grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null '
    f'|| {RAY_STATUS} || '
    f'{SKY_PIP_CMD} install --exists-action w -U ray[default]=={SKY_REMOTE_RAY_VERSION}; '  # pylint: disable=line-too-long
    # In some envs, e.g. pip does not have permission to write under /opt/conda
    # ray package will be installed under ~/.local/bin. If the user's PATH does
    # not include ~/.local/bin (the pip install will have the output: `WARNING:
    # The scripts ray, rllib, serve and tune are installed in '~/.local/bin'
    # which is not on PATH.`), causing an empty SKY_RAY_PATH_FILE later.
    #
    # Here, we add ~/.local/bin to the end of the PATH to make sure the issues
    # mentioned above are resolved.
    'export PATH=$PATH:$HOME/.local/bin; '
    # Writes ray path to file if it does not exist or the file is empty.
    f'[ -s {SKY_RAY_PATH_FILE} ] || '
    f'{{ {ACTIVATE_SKY_REMOTE_PYTHON_ENV} && '
    f'which ray > {SKY_RAY_PATH_FILE} || exit 1; }}; '
    # END ray package check and installation
    f'{{ {SKY_PIP_CMD} list | grep "skypilot " && '
    '[ "$(cat ~/.sky/wheels/current_sky_wheel_hash)" == "{sky_wheel_hash}" ]; } || '  # pylint: disable=line-too-long
    f'{{ {SKY_PIP_CMD} uninstall skypilot -y; '
    f'{SKY_PIP_CMD} install "$(echo ~/.sky/wheels/{{sky_wheel_hash}}/'
    f'skypilot-{_sky_version}*.whl)[{{cloud}}, remote]" && '
    'echo "{sky_wheel_hash}" > ~/.sky/wheels/current_sky_wheel_hash || '
    'exit 1; }; '
    # END SkyPilot package check and installation

    # Only patch ray when the ray version is the same as the expected version.
    # The ray installation above can be skipped due to the existing ray cluster
    # for backward compatibility. In this case, we should not patch the ray
    # files.
    f'{SKY_PIP_CMD} list | grep "ray " | grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null '
    f'&& {{ {SKY_PYTHON_CMD} -c "from sky.skylet.ray_patches import patch; patch()" '
    '|| exit 1; };')

# The name for the environment variable that stores SkyPilot user hash, which
# is mainly used to make sure sky commands runs on a VM launched by SkyPilot
# will be recognized as the same user (e.g., jobs controller or sky serve
# controller).
USER_ID_ENV_VAR = 'SKYPILOT_USER_ID'

# The name for the environment variable that stores SkyPilot user name.
# Similar to USER_ID_ENV_VAR, this is mainly used to make sure sky commands
# runs on a VM launched by SkyPilot will be recognized as the same user.
USER_ENV_VAR = 'SKYPILOT_USER'

# In most clouds, cluster names can only contain lowercase letters, numbers
# and hyphens. We use this regex to validate the cluster name.
CLUSTER_NAME_VALID_REGEX = '[a-zA-Z]([-_.a-zA-Z0-9]*[a-zA-Z0-9])?'

# Used for translate local file mounts to cloud storage. Please refer to
# sky/execution.py::_maybe_translate_local_file_mounts_and_sync_up for
# more details.
WORKDIR_BUCKET_NAME = 'skypilot-workdir-{username}-{id}'
FILE_MOUNTS_BUCKET_NAME = 'skypilot-filemounts-folder-{username}-{id}'
FILE_MOUNTS_FILE_ONLY_BUCKET_NAME = 'skypilot-filemounts-files-{username}-{id}'
FILE_MOUNTS_LOCAL_TMP_DIR = 'skypilot-filemounts-files-{id}'
FILE_MOUNTS_REMOTE_TMP_DIR = '/tmp/sky-{}-filemounts-files'

# The default idle timeout for SkyPilot controllers. This include spot
# controller and sky serve controller.
# TODO(tian): Refactor to controller_utils. Current blocker: circular import.
CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 10

# Due to the CPU/memory usage of the controller process launched with sky jobs (
# use ray job under the hood), we need to reserve some CPU/memory for each jobs/
# serve controller process.
# Jobs: A default controller with 8 vCPU and 32 GB memory can manage up to 32
# managed jobs.
# Serve: A default controller with 4 vCPU and 16 GB memory can run up to 16
# services.
CONTROLLER_PROCESS_CPU_DEMAND = 0.25

# SkyPilot environment variables
SKYPILOT_NUM_NODES = 'SKYPILOT_NUM_NODES'
SKYPILOT_NODE_IPS = 'SKYPILOT_NODE_IPS'
SKYPILOT_NUM_GPUS_PER_NODE = 'SKYPILOT_NUM_GPUS_PER_NODE'
SKYPILOT_NODE_RANK = 'SKYPILOT_NODE_RANK'

# Placeholder for the SSH user in proxy command, replaced when the ssh_user is
# known after provisioning.
SKY_SSH_USER_PLACEHOLDER = 'skypilot:ssh_user'

# The keys that can be overridden in the `~/.sky/config.yaml` file. The
# overrides are specified in task YAMLs.
OVERRIDEABLE_CONFIG_KEYS: List[Tuple[str, ...]] = [
    ('docker', 'run_options'),
    ('nvidia_gpus', 'disable_ecc'),
    ('kubernetes', 'pod_config'),
    ('kubernetes', 'provision_timeout'),
    ('gcp', 'managed_instance_group'),
]

# Constants for Azure blob storage
WAIT_FOR_STORAGE_ACCOUNT_CREATION = 60
# Observed time for new role assignment to propagate was ~45s
WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT = 180
RETRY_INTERVAL_AFTER_ROLE_ASSIGNMENT = 10
ROLE_ASSIGNMENT_FAILURE_ERROR_MSG = (
    'Failed to assign Storage Blob Data Owner role to the '
    'storage account {storage_account_name}.')
