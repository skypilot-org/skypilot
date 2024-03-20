"""Constants for SkyPilot."""
from packaging import version

import sky

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
SKY_REMOTE_RAY_VERSION = '2.9.3'

# The name for the environment variable that stores the unique ID of the
# current task. This will stay the same across multiple recoveries of the
# same spot task.
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

# Install conda on the remote cluster if it is not already installed.
# We use conda with python 3.10 to be consistent across multiple clouds with
# best effort.
# https://github.com/ray-project/ray/issues/31606
# We use python 3.10 to be consistent with the python version of the
# AWS's Deep Learning AMI's default conda environment.
CONDA_INSTALLATION_COMMANDS = (
    'which conda > /dev/null 2>&1 || '
    '(wget -nc https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-x86_64.sh -O Miniconda3-Linux-x86_64.sh && '  # pylint: disable=line-too-long
    'bash Miniconda3-Linux-x86_64.sh -b && '
    'eval "$(~/miniconda3/bin/conda shell.bash hook)" && conda init && '
    'conda config --set auto_activate_base true); '
    'grep "# >>> conda initialize >>>" ~/.bashrc || conda init;')

_sky_version = str(version.parse(sky.__version__))
RAY_STATUS = f'RAY_ADDRESS=127.0.0.1:{SKY_REMOTE_RAY_PORT} ray status'
# Install ray and skypilot on the remote cluster if they are not already
# installed. {var} will be replaced with the actual value in
# backend_utils.write_cluster_config.
RAY_SKYPILOT_INSTALLATION_COMMANDS = (
    '(type -a python | grep -q python3) || '
    'echo \'alias python=python3\' >> ~/.bashrc;'
    '(type -a pip | grep -q pip3) || echo \'alias pip=pip3\' >> ~/.bashrc;'
    'mkdir -p ~/sky_workdir && mkdir -p ~/.sky/sky_app;'
    'source ~/.bashrc;'
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
    f'pip3 list | grep "ray " | grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null '
    f'|| {RAY_STATUS} || '
    f'pip3 install --exists-action w -U ray[default]=={SKY_REMOTE_RAY_VERSION}; '  # pylint: disable=line-too-long
    # END ray package check and installation
    '{ pip3 list | grep "skypilot " && '
    '[ "$(cat ~/.sky/wheels/current_sky_wheel_hash)" == "{sky_wheel_hash}" ]; } || '  # pylint: disable=line-too-long
    '{ pip3 uninstall skypilot -y; '
    'pip3 install "$(echo ~/.sky/wheels/{sky_wheel_hash}/'
    f'skypilot-{_sky_version}*.whl)[{{cloud}}, remote]" && '
    'echo "{sky_wheel_hash}" > ~/.sky/wheels/current_sky_wheel_hash || '
    'exit 1; }; '
    # END SkyPilot package check and installation

    # Only patch ray when the ray version is the same as the expected version.
    # The ray installation above can be skipped due to the existing ray cluster
    # for backward compatibility. In this case, we should not patch the ray
    # files.
    f'pip3 list | grep "ray " | grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null '
    '&& { python3 -c "from sky.skylet.ray_patches import patch; patch()" '
    '|| exit 1; };')

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

# Due to the CPU/memory usage of the controller process launched with sky job (
# use ray job under the hood), we need to reserve some CPU/memory for each spot/
# serve controller process.
# Spot: A default controller with 8 vCPU and 32 GB memory can manage up to 32
# spot jobs.
# Serve: A default controller with 4 vCPU and 16 GB memory can run up to 16
# services.
CONTROLLER_PROCESS_CPU_DEMAND = 0.25
