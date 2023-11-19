"""Constants for SkyPilot."""

SKY_LOGS_DIRECTORY = '~/sky_logs'
SKY_REMOTE_WORKDIR = '~/sky_workdir'

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
SKY_REMOTE_RAY_VERSION = '2.4.0'
SKY_REMOTE_WHEEL_PATH = '~/.sky/wheels'
SKY_REMOTE_PYTHON_ENV = '~/skypilot-runtime'

# TODO(mluo): Make explicit `sky launch -c <name> ''` optional.
UNINITIALIZED_ONPREM_CLUSTER_MESSAGE = (
    'Found uninitialized local cluster {cluster}. Run this '
    'command to initialize it locally: sky launch -c {cluster} \'\'')

# The name for the environment variable that stores the unique ID of the
# current task. This will stay the same across multiple recoveries of the
# same spot task.
# TODO(zhwu): Remove SKYPILOT_JOB_ID after 0.5.0.
TASK_ID_ENV_VAR_DEPRECATED = 'SKYPILOT_JOB_ID'
TASK_ID_ENV_VAR = 'SKYPILOT_TASK_ID'
# This environment variable stores a '\n'-separated list of task IDs that
# are within the same spot job (DAG). This can be used by the user to
# retrieve the task IDs of any tasks that are within the same spot job.
# This environment variable is pre-assigned before any task starts
# running within the same job, and will remain constant throughout the
# lifetime of the job.
TASK_ID_LIST_ENV_VAR = 'SKYPILOT_TASK_IDS'

# The version of skylet. MUST bump this version whenever we need the skylet to
# be restarted on existing clusters updated with the new version of SkyPilot,
# e.g., when we add new events to skylet, or we fix a bug in skylet.
#
# TODO(zongheng,zhanghao): make the upgrading of skylet automatic?
SKYLET_VERSION = '4'
SKYLET_VERSION_FILE = '~/.sky/skylet_version'

# `sky spot dashboard`-related
#
# Port on the remote spot controller that the dashboard is running on.
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

ACTIVATE_PYTHON_ENV = (f'[ -d {SKY_REMOTE_PYTHON_ENV} ] && '
                       f'source {SKY_REMOTE_PYTHON_ENV}/bin/active;')


def run_in_python_env(command):
    """Returns a command that runs in the SkyPilot's python environment."""
    return f'( {ACTIVATE_PYTHON_ENV}{command}; deactivate )'


# Install conda on the remote cluster if it is not already installed.
# We do not install the latest conda with python 3.11 because ray has not
# officially supported it yet.
# https://github.com/ray-project/ray/issues/31606
# We use python 3.10 to be consistent with the python version of the
# AWS's Deep Learning AMI's default conda environment.
_RUN_PYTHON = run_in_python_env('python \\$@')
_RUN_PIP = run_in_python_env('pip \\$@')
_RUN_RAY = run_in_python_env('ray \\$@')
CONDA_INSTALLATION_COMMANDS = (
    'which conda > /dev/null 2>&1 || '
    '{ wget -nc https://repo.anaconda.com/miniconda/Miniconda3-py310_23.5.2-0-Linux-x86_64.sh -O Miniconda3-Linux-x86_64.sh && '  # pylint: disable=line-too-long
    'bash Miniconda3-Linux-x86_64.sh -b && '
    'eval "$(~/miniconda3/bin/conda shell.bash hook)" && conda init && '
    'conda config --set auto_activate_base true && source ~/.bashrc; }; '
    # Only run `conda init` if the conda is not installed under /opt/conda,
    # which is the case for VMs created on GCP, and running `conda init` will
    # cause error and waiting for the error to be reported: #2273.
    'which conda | grep /opt/conda || conda init > /dev/null;'
    # Create a separate conda environment for SkyPilot dependencies.
    f'[ -d {SKY_REMOTE_PYTHON_ENV} ] || '
    f'python -m venv {SKY_REMOTE_PYTHON_ENV}; '
    f'source {SKY_REMOTE_PYTHON_ENV}/bin/activate; '
    f'echo "function skypy () {{ {_RUN_PYTHON} }}" >> ~/.bashrc;'
    f'echo "function skypip () {{ {_RUN_PIP} }}" >> ~/.bashrc;'
    f'echo "function skyray () {{ {_RUN_RAY} }}" >> ~/.bashrc;')

RAY_AND_SKYPILOT_SETUP_COMMANDS = (
    '(type -a python | grep -q python3) || '
    'echo "alias python=python3" >> ~/.bashrc;'
    '(type -a pip | grep -q pip3) || echo "alias pip=pip3" >> ~/.bashrc;'
    'mkdir -p ~/sky_workdir && mkdir -p ~/.sky/sky_app && '
    'touch ~/.sudo_as_admin_successful;'
    f'(pip list | grep "ray " | grep "{SKY_REMOTE_RAY_VERSION}" '
    '2>&1 > /dev/null || '
    f'pip install --exists-action w -U '
    f'ray[default]=={SKY_REMOTE_RAY_VERSION});'
    f'(pip list | grep "skypilot " && '
    f'[ "$(cat {SKY_REMOTE_WHEEL_PATH}/current_sky_wheel_hash)" == '
    f'"{{sky_wheel_hash}}" ]) || (pip uninstall skypilot -y; '
    f'pip install "$(echo {SKY_REMOTE_WHEEL_PATH}/'
    f'{{sky_wheel_hash}}/skypilot-{{sky_version}}*.whl)[{{cloud}}, remote]" && '
    f'echo "{{sky_wheel_hash}}" '
    f'> {SKY_REMOTE_WHEEL_PATH}/current_sky_wheel_hash || exit 1);'
    f'python -c '
    '"from sky.skylet.ray_patches import patch; patch()" || exit 1;')

# The name for the environment variable that stores SkyPilot user hash, which
# is mainly used to make sure sky commands runs on a VM launched by SkyPilot
# will be recognized as the same user (e.g., spot controller or sky serve
# controller).
USER_ID_ENV_VAR = 'SKYPILOT_USER_ID'

# The name for the environment variable that stores SkyPilot user name.
# Similar to USER_ID_ENV_VAR, this is mainly used to make sure sky commands
# runs on a VM launched by SkyPilot will be recognized as the same user.
USER_ENV_VAR = 'SKYPILOT_USER'

# In most clouds, cluster names can only contain lowercase letters, numbers
# and hyphens. We use this regex to validate the cluster name.
CLUSTER_NAME_VALID_REGEX = '[a-z]([-a-z0-9]*[a-z0-9])?'

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
