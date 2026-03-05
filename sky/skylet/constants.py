"""Constants for SkyPilot."""
from typing import List, Tuple

from packaging import version

import sky
from sky.setup_files import dependencies

# The base directory for all SkyPilot runtime artifacts.
# Historically, we have always used $HOME, but we couldn't
# do that for Slurm, because $HOME typically points to a NFS
# mounted directory, which does not work well with SQLite.
# https://sqlite.org/faq.html#q5
# Additionally, having the skypilot-runtime python venv be
# on an NFS makes things very slow.
SKY_RUNTIME_DIR = '${SKY_RUNTIME_DIR:-$HOME}'
# Same as above but for use within python code instead of shell commands.
# Example usage:
# os.path.join(
#    os.path.expanduser(os.environ.get(SKY_RUNTIME_DIR_ENV_VAR_KEY, '~')),
#    '.sky/jobs.db')
SKY_RUNTIME_DIR_ENV_VAR_KEY = 'SKY_RUNTIME_DIR'
SKY_CLUSTER_NAME_ENV_VAR_KEY = 'SKY_CLUSTER_NAME'
# We keep sky_logs and sky_workdir in $HOME, because
# these are artifacts that users can access, and having
# them be in $HOME makes it more convenient.
SKY_LOGS_DIRECTORY = '~/sky_logs'
SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_TEMPLATES_DIRECTORY = '~/sky_templates'
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
SKY_REMOTE_RAY_PORT_FILE = '.sky/ray_port.json'
SKY_REMOTE_RAY_TEMPDIR = '/tmp/ray_skypilot'
SKY_REMOTE_RAY_VERSION = '2.9.3'

# To avoid user image causing issue with the SkyPilot runtime, we run SkyPilot
# commands the following prefix:
# 1. env -u PYTHONPATH: unset PYTHONPATH to avoid any package specified in
# PYTHONPATH interfering with the SkyPilot runtime.
# 2. env -C $HOME: set the execution directory to $HOME to avoid the case when
# a user's WORKDIR in Dockerfile is a Python site-packages directory. Python
# adds CWD to the beginning of sys.path, so if WORKDIR contains packages (e.g.,
# compiled for a different Python version), imports will fail with errors like
# "ModuleNotFoundError: No module named 'rpds.rpds'".
#
# TODO(zhwu): Switch -C $HOME to PYTHONSAFEPATH=1, once we moved our runtime to
# Python 3.11 for a more robust setup.
SKY_UNSET_PYTHONPATH_AND_SET_CWD = 'env -u PYTHONPATH -C $HOME'
# We store the absolute path of the python executable (/opt/conda/bin/python3)
# in this file, so that any future internal commands that need to use python
# can use this path. This is useful for the case where the user has a custom
# conda environment as a default environment, which is not the same as the one
# used for installing SkyPilot runtime (ray and skypilot).
SKY_PYTHON_PATH_FILE = f'{SKY_RUNTIME_DIR}/.sky/python_path'
SKY_RAY_PATH_FILE = f'{SKY_RUNTIME_DIR}/.sky/ray_path'
SKY_GET_PYTHON_PATH_CMD = (f'[ -s {SKY_PYTHON_PATH_FILE} ] && '
                           f'cat {SKY_PYTHON_PATH_FILE} 2> /dev/null || '
                           'which python3')
# Python executable, e.g., /opt/conda/bin/python3
SKY_PYTHON_CMD = (f'{SKY_UNSET_PYTHONPATH_AND_SET_CWD} '
                  f'$({SKY_GET_PYTHON_PATH_CMD})')
# Prefer SKY_UV_PIP_CMD, which is faster.
# TODO(cooperc): remove remaining usage (GCP TPU setup).
SKY_PIP_CMD = f'{SKY_PYTHON_CMD} -m pip'
# Ray executable, e.g., /opt/conda/bin/ray
# We need to add SKY_PYTHON_CMD before ray executable because:
# The ray executable is a python script with a header like:
#   #!/opt/conda/bin/python3
SKY_RAY_CMD = (f'{SKY_PYTHON_CMD} $([ -s {SKY_RAY_PATH_FILE} ] && '
               f'cat {SKY_RAY_PATH_FILE} 2> /dev/null || which ray)')

# Use $(which env) to find env, falling back to /usr/bin/env if which is
# unavailable. This works around a Slurm quirk where srun's execvp() doesn't
# check execute permissions, failing when $HOME/.local/bin/env (non-executable,
# from uv installation) shadows /usr/bin/env.
SKY_SLURM_UNSET_PYTHONPATH = ('$(which env 2>/dev/null || echo /usr/bin/env) '
                              '-u PYTHONPATH')
SKY_SLURM_PYTHON_CMD = (f'{SKY_SLURM_UNSET_PYTHONPATH} '
                        f'$({SKY_GET_PYTHON_PATH_CMD})')

# Separate env for SkyPilot runtime dependencies.
SKY_REMOTE_PYTHON_ENV_NAME = 'skypilot-runtime'
SKY_REMOTE_PYTHON_ENV: str = f'{SKY_RUNTIME_DIR}/{SKY_REMOTE_PYTHON_ENV_NAME}'
ACTIVATE_SKY_REMOTE_PYTHON_ENV = f'source {SKY_REMOTE_PYTHON_ENV}/bin/activate'
# Place the conda root in the runtime directory, as installing to $HOME
# on an NFS takes too long (1-2m slower).
SKY_CONDA_ROOT = f'{SKY_RUNTIME_DIR}/miniconda3'
# uv is used for venv and pip, much faster than python implementations.
SKY_UV_INSTALL_DIR = '"$HOME/.local/bin"'
# set UV_SYSTEM_PYTHON to false in case the
# user provided docker image set it to true.
# unset PYTHONPATH in case the user provided docker image set it.
SKY_UV_CMD = ('UV_SYSTEM_PYTHON=false '
              f'{SKY_UNSET_PYTHONPATH_AND_SET_CWD} {SKY_UV_INSTALL_DIR}/uv')
# This won't reinstall uv if it's already installed, so it's safe to re-run.
SKY_UV_INSTALL_CMD = (f'{SKY_UV_CMD} -V >/dev/null 2>&1 || '
                      'curl -LsSf https://astral.sh/uv/install.sh '
                      f'| UV_INSTALL_DIR={SKY_UV_INSTALL_DIR} sh')
SKY_UV_PIP_CMD: str = (f'VIRTUAL_ENV={SKY_REMOTE_PYTHON_ENV} {SKY_UV_CMD} pip')
SKY_UV_RUN_CMD: str = (f'VIRTUAL_ENV={SKY_REMOTE_PYTHON_ENV} {SKY_UV_CMD} run '
                       '--no-project --no-config')
# Deleting the SKY_REMOTE_PYTHON_ENV_NAME from the PATH and unsetting relevant
# VIRTUAL_ENV envvars to deactivate the environment. `deactivate` command does
# not work when conda is used.
DEACTIVATE_SKY_REMOTE_PYTHON_ENV = (
    'export PATH='
    f'$(echo $PATH | sed "s|$(echo {SKY_REMOTE_PYTHON_ENV})/bin:||") && '
    'unset VIRTUAL_ENV && unset VIRTUAL_ENV_PROMPT')

# Prefix for SkyPilot environment variables
SKYPILOT_ENV_VAR_PREFIX = 'SKYPILOT_'
SKYPILOT_SERVER_ENV_VAR_PREFIX = 'SKYPILOT_SERVER_'

# The name for the environment variable that stores the unique ID of the
# current task. This will stay the same across multiple recoveries of the
# same managed task.
TASK_ID_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}TASK_ID'
# This environment variable stores a '\n'-separated list of task IDs that
# are within the same managed job (DAG). This can be used by the user to
# retrieve the task IDs of any tasks that are within the same managed job.
# This environment variable is pre-assigned before any task starts
# running within the same job, and will remain constant throughout the
# lifetime of the job.
TASK_ID_LIST_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}TASK_IDS'

# The version of skylet. MUST bump this version whenever we need the skylet to
# be restarted on existing clusters updated with the new version of SkyPilot,
# e.g., when we add new events to skylet, we fix a bug in skylet, or skylet
# needs to load the new version of SkyPilot code to handle the autostop when the
# cluster yaml is updated.
#
# TODO(zongheng,zhanghao): make the upgrading of skylet automatic?
SKYLET_VERSION = '34'  # Add fields to ManagedJobInfo proto for GPU metrics.
# The version of the lib files that skylet/jobs use. Whenever there is an API
# change for the job_lib or log_lib, we need to bump this version, so that the
# user can be notified to update their SkyPilot version on the remote cluster.
SKYLET_LIB_VERSION = 6  # Add better support for launching many jobs at once.
SKYLET_VERSION_FILE = '.sky/skylet_version'
SKYLET_LOG_FILE = '.sky/skylet.log'
SKYLET_PID_FILE = '.sky/skylet_pid'
SKYLET_PORT_FILE = '.sky/skylet_port'
SKYLET_GRPC_PORT = 46590
SKYLET_GRPC_TIMEOUT_SECONDS = 10
AUTOSTOP_HOOK_LOG_FILE = '.sky/autostop_hook.log'

# Autostop hook timeout default (1 hour in seconds)
DEFAULT_AUTOSTOP_HOOK_TIMEOUT_SECONDS = 3600

# Docker default options
DEFAULT_DOCKER_CONTAINER_NAME = 'sky_container'
DEFAULT_DOCKER_PORT = 10022
DOCKER_USERNAME_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}DOCKER_USERNAME'
DOCKER_PASSWORD_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}DOCKER_PASSWORD'
DOCKER_SERVER_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}DOCKER_SERVER'
DOCKER_LOGIN_ENV_VARS = {
    DOCKER_USERNAME_ENV_VAR,
    DOCKER_PASSWORD_ENV_VAR,
    DOCKER_SERVER_ENV_VAR,
}

RUNPOD_DOCKER_USERNAME_ENV_VAR = 'SKYPILOT_RUNPOD_DOCKER_USERNAME'

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

SETUP_SKY_DIRS_COMMANDS = (f'mkdir -p ~/sky_workdir && '
                           f'mkdir -p ~/.sky/sky_app && '
                           f'mkdir -p {SKY_RUNTIME_DIR}/.sky;')

# Install conda on the remote cluster if it is not already installed.
# We use conda with python 3.10 to be consistent across multiple clouds with
# best effort.
# https://github.com/ray-project/ray/issues/31606
# We use python 3.10 to be consistent with the python version of the
# AWS's Deep Learning AMI's default conda environment.
CONDA_INSTALLATION_COMMANDS = (
    'which conda > /dev/null 2>&1 || '
    '{ '
    # Use uname -m to get the architecture of the machine and download the
    # corresponding Miniconda3-Linux.sh script.
    # Download to /tmp to ensure write access for non-root users.
    'curl https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-$(uname -m).sh -o /tmp/Miniconda3-Linux.sh && '  # pylint: disable=line-too-long
    # We do not use && for installation of conda and the following init commands
    # because for some images, conda is already installed, but not initialized.
    # In this case, we need to initialize conda and set auto_activate_base to
    # true.
    '{ '
    f'bash /tmp/Miniconda3-Linux.sh -b -p "{SKY_CONDA_ROOT}" || true; '
    f'eval "$({SKY_CONDA_ROOT}/bin/conda shell.bash hook)" && conda init && '
    # Caller should replace {conda_auto_activate} with either true or false.
    'conda config --set auto_activate_base {conda_auto_activate} && '
    'conda activate base; }; '
    # If conda was not installed and the image is a docker image,
    # we deactivate any active conda environment we set.
    # Caller should replace {is_custom_docker} with either true or false.
    'if [ "{is_custom_docker}" = "true" ]; then '
    'conda deactivate;'
    'fi;'
    '}; '
    # run this command only if the image is not a docker image assuming
    # that if a user is using a docker image, they know what they are doing
    # in terms of conda setup/activation.
    # Caller should replace {is_custom_docker} with either true or false.
    'if [ "{is_custom_docker}" = "false" ]; then '
    'grep "# >>> conda initialize >>>" ~/.bashrc || '
    '{ conda init && source ~/.bashrc; };'
    'fi;')

UV_INSTALLATION_COMMANDS = (
    # Install uv for venv management and pip installation.
    f'{SKY_UV_INSTALL_CMD};'
    # Create a separate python environment for SkyPilot dependencies.
    f'[ -d {SKY_REMOTE_PYTHON_ENV} ] || '
    # Do NOT use --system-site-packages here, because if users upgrade any
    # packages in the base env, they interfere with skypilot dependencies.
    # Reference: https://github.com/skypilot-org/skypilot/issues/4097
    # --seed will include pip and setuptools, which are present in venvs created
    # with python -m venv.
    # --python 3.10 will ensure the specific python version is downloaded
    # and installed in the venv. SkyPilot requires Python<3.12, and 3.10 is
    # preferred. We have to always pass in `--python` to avoid the issue when a
    # user has `.python_version` file in their home directory, which will cause
    # uv to use the python version specified in the `.python_version` file.
    # TODO(zhwu): consider adding --python-preference only-managed to avoid
    # using the system python, if a user report such issue.
    f'{SKY_UV_CMD} venv --seed {SKY_REMOTE_PYTHON_ENV} --python 3.10;'
    f'echo "$(echo {SKY_REMOTE_PYTHON_ENV})/bin/python" > {SKY_PYTHON_PATH_FILE};'  # pylint: disable=line-too-long
)

_sky_version = str(version.parse(sky.__version__))
RAY_STATUS = f'RAY_ADDRESS=127.0.0.1:{SKY_REMOTE_RAY_PORT} {SKY_RAY_CMD} status'
RAY_INSTALLATION_COMMANDS = (
    f'{SKY_UV_INSTALL_CMD};'
    f'{SETUP_SKY_DIRS_COMMANDS}'
    # Print the PATH in provision.log to help debug PATH issues.
    'echo PATH=$PATH; '
    # Install setuptools<=69.5.1 to avoid the issue with the latest setuptools
    # causing the error:
    #   ImportError: cannot import name 'packaging' from 'pkg_resources'"
    f'{SKY_UV_PIP_CMD} install "setuptools<70"; '
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
    f'{SKY_UV_PIP_CMD} list | grep "ray " | '
    f'grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null '
    f'|| {RAY_STATUS} || '
    # The pydantic-core==2.41.3 for arm seems corrupted
    # so we need to avoid that specific version.
    f'{SKY_UV_PIP_CMD} install -U "ray[default]=={SKY_REMOTE_RAY_VERSION}" "pydantic-core==2.41.1"; '  # pylint: disable=line-too-long
    # In some envs, e.g. pip does not have permission to write under /opt/conda
    # ray package will be installed under ~/.local/bin. If the user's PATH does
    # not include ~/.local/bin (the pip install will have the output: `WARNING:
    # The scripts ray, rllib, serve and tune are installed in '~/.local/bin'
    # which is not on PATH.`), causing an empty SKY_RAY_PATH_FILE later.
    #
    # Here, we add ~/.local/bin to the end of the PATH to make sure the issues
    # mentioned above are resolved.
    f'export PATH=$PATH:{SKY_RUNTIME_DIR}/.local/bin; '
    # Writes ray path to file if it does not exist or the file is empty.
    f'[ -s {SKY_RAY_PATH_FILE} ] || '
    f'{{ {SKY_UV_RUN_CMD} '
    f'which ray > {SKY_RAY_PATH_FILE} || exit 1; }}; ')

# Copy SkyPilot templates from the installed wheel to ~/sky_templates.
# This must run after the skypilot wheel is installed.
# Note: We remove ~/sky_templates first to avoid import conflicts where Python
# would import from ~/sky_templates instead of site-packages (because
# sky_templates itself is a package), leading to src == dst error when
# launching on an existing cluster.
COPY_SKYPILOT_TEMPLATES_COMMANDS = (
    f'rm -rf {SKY_TEMPLATES_DIRECTORY}; '
    f'{ACTIVATE_SKY_REMOTE_PYTHON_ENV}; '
    f'{SKY_PYTHON_CMD} -c \''
    'import sky_templates, shutil, os; '
    'src = os.path.dirname(sky_templates.__file__); '
    f'dst = os.path.expanduser(\"{SKY_TEMPLATES_DIRECTORY}\"); '
    'print(f\"Copying templates from {src} to {dst}...\"); '
    'shutil.copytree(src, dst); '
    'print(f\"Templates copied successfully\")\'; '
    # Make scripts executable.
    f'find {SKY_TEMPLATES_DIRECTORY} -type f ! -name "*.py" ! -name "*.md" '
    '-exec chmod +x {} + ; ')

SKYPILOT_WHEEL_INSTALLATION_COMMANDS = (
    f'{SKY_UV_INSTALL_CMD};'
    f'{{ {SKY_UV_PIP_CMD} list | grep "skypilot " && '
    '[ "$(cat ~/.sky/wheels/current_sky_wheel_hash)" == "{sky_wheel_hash}" ]; } || '  # pylint: disable=line-too-long
    f'{{ {SKY_UV_PIP_CMD} uninstall skypilot; '
    # uv cannot install azure-cli normally, since it depends on pre-release
    # packages. Manually install azure-cli with the --prerelease=allow flag
    # first. This will allow skypilot to successfully install. See
    # https://docs.astral.sh/uv/pip/compatibility/#pre-release-compatibility.
    # We don't want to use --prerelease=allow for all packages, because it will
    # cause uv to use pre-releases for some other packages that have sufficient
    # stable releases.
    'if [ "{cloud}" = "azure" ]; then '
    f'{SKY_UV_PIP_CMD} install --prerelease=allow "{dependencies.AZURE_CLI}";'
    'fi;'
    # Install skypilot from wheel
    f'{SKY_UV_PIP_CMD} install "$(echo ~/.sky/wheels/{{sky_wheel_hash}}/'
    f'skypilot-{_sky_version}*.whl)[{{cloud}}, remote]" && '
    'echo "{sky_wheel_hash}" > ~/.sky/wheels/current_sky_wheel_hash || '
    'exit 1; }; ')

# Install ray and skypilot on the remote cluster if they are not already
# installed. {var} will be replaced with the actual value in
# backend_utils.write_cluster_config.
RAY_SKYPILOT_INSTALLATION_COMMANDS = (
    f'{RAY_INSTALLATION_COMMANDS} '
    f'{SKYPILOT_WHEEL_INSTALLATION_COMMANDS} '
    # Only patch ray when the ray version is the same as the expected version.
    # The ray installation above can be skipped due to the existing ray cluster
    # for backward compatibility. In this case, we should not patch the ray
    # files.
    f'{SKY_UV_PIP_CMD} list | grep "ray " | '
    f'grep {SKY_REMOTE_RAY_VERSION} 2>&1 > /dev/null && '
    f'{{ {SKY_PYTHON_CMD} -c '
    '"from sky.skylet.ray_patches import patch; patch()" || exit 1; }; ')

# The name for the environment variable that stores SkyPilot user hash, which
# is mainly used to make sure sky commands runs on a VM launched by SkyPilot
# will be recognized as the same user (e.g., jobs controller or sky serve
# controller).
USER_ID_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}USER_ID'

# The name for the environment variable that stores SkyPilot user name.
# Similar to USER_ID_ENV_VAR, this is mainly used to make sure sky commands
# runs on a VM launched by SkyPilot will be recognized as the same user.
USER_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}USER'

# SSH configuration to allow more concurrent sessions and connections.
# Default MaxSessions is 10.
# Default MaxStartups is 10:30:60, meaning:
#   - Up to 10 unauthenticated connections are allowed without restriction.
#   - From 11 to 60 connections, 30% are randomly dropped.
#   - Above 60 connections, all are dropped.
# These defaults are too low for submitting many parallel jobs (e.g., 150),
# which can easily exceed the limits and cause connection failures.
# The new values (MaxSessions 200, MaxStartups 150:30:200) increase these
# limits significantly.
# TODO(zeping): Bake this configuration in SkyPilot default images.
SET_SSH_MAX_SESSIONS_CONFIG_CMD = (
    'sudo bash -c \''
    'echo "MaxSessions 200" >> /etc/ssh/sshd_config; '
    'echo "MaxStartups 150:30:200" >> /etc/ssh/sshd_config; '
    '(systemctl reload sshd || service ssh reload); '
    '\'')

# Internal: Env var indicating the system is running with a remote API server.
# It is used for internal purposes, including the jobs controller to mark
# clusters as launched with a remote API server.
USING_REMOTE_API_SERVER_ENV_VAR = (
    f'{SKYPILOT_ENV_VAR_PREFIX}USING_REMOTE_API_SERVER')

# In most clouds, cluster names can only contain lowercase letters, numbers
# and hyphens. We use this regex to validate the cluster name.
CLUSTER_NAME_VALID_REGEX = '[a-zA-Z]([-_.a-zA-Z0-9]*[a-zA-Z0-9])?'

# Recipe names: letters, numbers, and dashes only (no underscores or dots).
# Must start with a letter, end with an alphanumeric character.
RECIPE_NAME_VALID_REGEX = r'[a-zA-Z]([-a-zA-Z0-9]*[a-zA-Z0-9])?'
RECIPE_NAME_MAX_LENGTH = 40

# Used for translate local file mounts to cloud storage. Please refer to
# sky/execution.py::_maybe_translate_local_file_mounts_and_sync_up for
# more details.
FILE_MOUNTS_BUCKET_NAME = 'skypilot-filemounts-{username}-{user_hash}-{id}'
FILE_MOUNTS_LOCAL_TMP_DIR = 'skypilot-filemounts-files-{id}'
FILE_MOUNTS_REMOTE_TMP_DIR = '/tmp/sky-{}-filemounts-files'
# For API server, the use a temporary directory in the same path as the upload
# directory to avoid using a different block device, which may not allow hard
# linking. E.g., in our API server deployment on k8s, ~/.sky/ is mounted from a
# persistent volume, so any contents in ~/.sky/ cannot be hard linked elsewhere.
FILE_MOUNTS_LOCAL_TMP_BASE_PATH = '~/.sky/tmp/'
# Base path for two-hop file mounts translation. See
# controller_utils.translate_local_file_mounts_to_two_hop().
FILE_MOUNTS_CONTROLLER_TMP_BASE_PATH = '~/.sky/tmp/controller'

# For passing in CPU and memory limits to the controller pod when running
# in k8s. Right now, we only use this for the jobs controller, but we may
# use this for the serve controller as well in the future.
# These files are written to disk by the skylet, who reads it from env vars
# passed by the backend when starting the skylet (start_skylet_on_head_node).
CONTROLLER_K8S_CPU_FILE = '~/.sky/_internal_k8s_pod_cpu'
CONTROLLER_K8S_MEMORY_FILE = '~/.sky/_internal_k8s_pod_memory'

# Used when an managed jobs are created and
# files are synced up to the cloud.
FILE_MOUNTS_WORKDIR_SUBPATH = 'job-{run_id}/workdir'
FILE_MOUNTS_SUBPATH = 'job-{run_id}/local-file-mounts/{i}'
FILE_MOUNTS_TMP_SUBPATH = 'job-{run_id}/tmp-files'

# Due to the CPU/memory usage of the controller process launched with sky jobs (
# use ray job under the hood), we need to reserve some CPU/memory for each jobs/
# serve controller process.
# Jobs: A default controller with 8 vCPU and 32 GB memory can manage up to 32
# managed jobs.
# Serve: A default controller with 4 vCPU and 16 GB memory can run up to 16
# services.
CONTROLLER_PROCESS_CPU_DEMAND = 0.25
# The log for SkyPilot API server.
API_SERVER_LOGS = '~/.sky/api_server/server.log'
# The lock for creating the SkyPilot API server.
API_SERVER_CREATION_LOCK_PATH = '~/.sky/api_server/.creation.lock'

# The name for the environment variable that stores the URL of the SkyPilot
# API server.
SKY_API_SERVER_URL_ENV_VAR = f'{SKYPILOT_ENV_VAR_PREFIX}API_SERVER_ENDPOINT'

# The name for the environment variable that stores the SkyPilot service
# account token on client side.
SERVICE_ACCOUNT_TOKEN_ENV_VAR = (
    f'{SKYPILOT_ENV_VAR_PREFIX}SERVICE_ACCOUNT_TOKEN')

# SkyPilot environment variables
SKYPILOT_NUM_NODES = f'{SKYPILOT_ENV_VAR_PREFIX}NUM_NODES'
SKYPILOT_NODE_IPS = f'{SKYPILOT_ENV_VAR_PREFIX}NODE_IPS'
SKYPILOT_SETUP_NUM_GPUS_PER_NODE = (
    f'{SKYPILOT_ENV_VAR_PREFIX}SETUP_NUM_GPUS_PER_NODE')
SKYPILOT_NUM_GPUS_PER_NODE = f'{SKYPILOT_ENV_VAR_PREFIX}NUM_GPUS_PER_NODE'
SKYPILOT_NODE_RANK = f'{SKYPILOT_ENV_VAR_PREFIX}NODE_RANK'

# Placeholder for the SSH user in proxy command, replaced when the ssh_user is
# known after provisioning.
SKY_SSH_USER_PLACEHOLDER = 'skypilot:ssh_user'

RCLONE_CONFIG_DIR = '~/.config/rclone'
RCLONE_CONFIG_PATH = f'{RCLONE_CONFIG_DIR}/rclone.conf'
RCLONE_MOUNT_CACHED_LOG_DIR = '~/.sky/rclone_log'
RCLONE_CACHE_DIR = '~/.cache/rclone'
RCLONE_CACHE_REFRESH_INTERVAL = 10

# The keys that can be overridden in the `~/.sky/config.yaml` file. The
# overrides are specified in task YAMLs.
OVERRIDEABLE_CONFIG_KEYS_IN_TASK: List[Tuple[str, ...]] = [
    ('docker', 'run_options'),
    ('nvidia_gpus', 'disable_ecc'),
    ('ssh', 'custom_metadata'),
    ('ssh', 'pod_config'),
    ('ssh', 'provision_timeout'),
    ('kubernetes', 'custom_metadata'),
    ('kubernetes', 'pod_config'),
    ('kubernetes', 'provision_timeout'),
    ('kubernetes', 'dws'),
    ('kubernetes', 'kueue'),
    ('kubernetes', 'remote_identity'),
    ('gcp', 'managed_instance_group'),
    ('gcp', 'enable_gvnic'),
    ('gcp', 'enable_gpu_direct'),
    ('gcp', 'placement_policy'),
    ('vast', 'datacenter_only'),
    ('vast', 'create_instance_kwargs'),
    ('active_workspace',),
]
# When overriding the SkyPilot configs on the API server with the client one,
# we skip the following keys because they are meant to be client-side configs.
# Also, we skip the consolidation mode config as those should be only set on
# the API server side.
SKIPPED_CLIENT_OVERRIDE_KEYS: List[Tuple[str, ...]] = [
    ('api_server',),
    ('allowed_clouds',),
    ('workspaces',),
    ('db',),
    ('daemons',),
    # TODO(kevin,tian): Override the whole controller config once our test
    # infrastructure supports setting dynamic server side configs.
    # Tests that are affected:
    # - test_managed_jobs_ha_kill_starting
    # - test_managed_jobs_ha_kill_running
    # - all tests that use LOW_CONTROLLER_RESOURCE_ENV or
    #   LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG (won't cause test failure,
    #   but the configs won't be applied)
    ('jobs', 'controller', 'consolidation_mode'),
    ('serve', 'controller', 'consolidation_mode'),
    ('jobs', 'controller', 'controller_logs_gc_retention_hours'),
    ('jobs', 'controller', 'task_logs_gc_retention_hours'),
]

# Constants for Azure blob storage
WAIT_FOR_STORAGE_ACCOUNT_CREATION = 60
# Observed time for new role assignment to propagate was ~45s
WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT = 180
RETRY_INTERVAL_AFTER_ROLE_ASSIGNMENT = 10
ROLE_ASSIGNMENT_FAILURE_ERROR_MSG = (
    'Failed to assign Storage Blob Data Owner role to the '
    'storage account {storage_account_name}.')

# Constants for path in K8S pod to store persistent setup and run scripts
# so that we can run them again after the pod restarts.
# Path within user home. For HA controller, assumes home directory is
# persistent through PVC. See kubernetes-ray.yml.j2.
PERSISTENT_SETUP_SCRIPT_PATH = '~/.sky/.controller_recovery_setup_commands.sh'
PERSISTENT_RUN_SCRIPT_DIR = '~/.sky/.controller_recovery_task_run'
# Signal file to indicate that the controller is recovering from a failure.
# See sky/jobs/utils.py::update_managed_jobs_statuses for more details.
PERSISTENT_RUN_RESTARTING_SIGNAL_FILE = (
    '~/.sky/.controller_recovery_restarting_signal')

HA_PERSISTENT_RECOVERY_LOG_PATH = '/tmp/{}ha_recovery.log'

# The placeholder for the local skypilot config path in file mounts for
# controllers.
LOCAL_SKYPILOT_CONFIG_PATH_PLACEHOLDER = 'skypilot:local_skypilot_config_path'

# Path to the generated cluster config yamls and ssh configs.
SKY_USER_FILE_PATH = '~/.sky/generated'

# TODO(cooperc): Update all env vars to begin with SKYPILOT_ or SKYPILOT_SERVER_
# Environment variable that is set to 'true' if this is a skypilot server.
ENV_VAR_IS_SKYPILOT_SERVER = 'IS_SKYPILOT_SERVER'
OVERRIDE_CONSOLIDATION_MODE = 'IS_SKYPILOT_JOB_CONTROLLER'
IS_SKYPILOT_SERVE_CONTROLLER = 'IS_SKYPILOT_SERVE_CONTROLLER'
# Environment variable that is set to 'true' if rolling update strategy is
# enabled for the API server deployment.
SKYPILOT_ROLLING_UPDATE_ENABLED = 'SKYPILOT_ROLLING_UPDATE_ENABLED'
# Environment variable that is set to 'true' if persistent storage is enabled
# for the API server deployment (via Helm storage.enabled=true).
# This enables persistence of managed job logs and file mounts across rolling
# updates.
SKYPILOT_API_SERVER_STORAGE_ENABLED = 'SKYPILOT_API_SERVER_STORAGE_ENABLED'

SERVE_OVERRIDE_CONCURRENT_LAUNCHES = (
    f'{SKYPILOT_ENV_VAR_PREFIX}SERVE_OVERRIDE_CONCURRENT_LAUNCHES')

# Environment variable that is set to 'true' if metrics are enabled.
ENV_VAR_SERVER_METRICS_ENABLED = 'SKY_API_SERVER_METRICS_ENABLED'

# If set, overrides the header that we can use to get the user name.
ENV_VAR_SERVER_AUTH_USER_HEADER = f'{SKYPILOT_ENV_VAR_PREFIX}AUTH_USER_HEADER'

# Environment variable that is used as the DB connection string for the
# skypilot server.
ENV_VAR_DB_CONNECTION_URI = (f'{SKYPILOT_ENV_VAR_PREFIX}DB_CONNECTION_URI')

# Environment variable that is set to 'true' if basic
# authentication is enabled in the API server.
ENV_VAR_ENABLE_BASIC_AUTH = 'ENABLE_BASIC_AUTH'
SKYPILOT_INITIAL_BASIC_AUTH = 'SKYPILOT_INITIAL_BASIC_AUTH'
SKYPILOT_INGRESS_BASIC_AUTH_ENABLED = 'SKYPILOT_INGRESS_BASIC_AUTH_ENABLED'
SKYPILOT_DISABLE_BASIC_AUTH_MIDDLEWARE = (
    'SKYPILOT_DISABLE_BASIC_AUTH_MIDDLEWARE')
ENV_VAR_ENABLE_SERVICE_ACCOUNTS = 'ENABLE_SERVICE_ACCOUNTS'

# Enable debug logging for requests.
ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING = (
    f'{SKYPILOT_SERVER_ENV_VAR_PREFIX}ENABLE_REQUEST_DEBUG_LOGGING')

SKYPILOT_DEFAULT_WORKSPACE = 'default'

# BEGIN constants used for service catalog.
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
HOSTED_CATALOG_DIR_URL_S3_MIRROR = 'https://skypilot-catalog.s3.us-east-1.amazonaws.com/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v8'
CATALOG_DIR = '~/.sky/catalogs'
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vast', 'vsphere', 'cudo', 'fluidstack',
              'paperspace', 'primeintellect', 'do', 'nebius', 'ssh', 'slurm',
              'hyperbolic', 'seeweb', 'shadeform', 'yotta')
# END constants used for service catalog.

# The user ID of the SkyPilot system.
SKYPILOT_SYSTEM_USER_ID = 'skypilot-system'

# The directory to store the logging configuration.
LOGGING_CONFIG_DIR = '~/.sky/logging'

# Resources constants
TIME_UNITS = {
    'm': 1,
    'h': 60,
    'd': 24 * 60,
    'w': 7 * 24 * 60,
}

# Time units for seconds-based duration parsing (for termination_delay, etc.)
# This includes 's' for seconds, which is not in TIME_UNITS (minutes-based).
TIME_UNITS_SECONDS = {
    's': 1,
    'm': 60,
    'h': 3600,
    'd': 86400,
    'w': 604800,
}


def _make_time_pattern(units: dict) -> str:
    """Create a regex pattern for time duration strings."""
    unit_pattern = '|'.join([unit.lower() for unit in units] +
                            [unit.upper() for unit in units])
    return f'^[0-9]+({unit_pattern})?$'


TIME_PATTERN: str = _make_time_pattern(TIME_UNITS)
TIME_PATTERN_SECONDS: str = _make_time_pattern(TIME_UNITS_SECONDS)

MEMORY_SIZE_UNITS = {
    'kb': 2**10,
    'ki': 2**10,
    'mb': 2**20,
    'mi': 2**20,
    'gb': 2**30,
    'gi': 2**30,
    'tb': 2**40,
    'ti': 2**40,
    'pb': 2**50,
    'pi': 2**50,
}

MEMORY_SIZE_PATTERN = (
    '^[0-9]+('
    f'{"|".join([unit.lower() for unit in MEMORY_SIZE_UNITS])}|'
    f'{"|".join([unit.upper() for unit in MEMORY_SIZE_UNITS])}|'
    f'{"|".join([unit[0].upper() + unit[1:] for unit in MEMORY_SIZE_UNITS if len(unit) > 1])}'  # pylint: disable=line-too-long
    ')?$')

LAST_USE_TRUNC_LENGTH = 25
USED_BY_TRUNC_LENGTH = 25
ERROR_MESSAGE_TRUNC_LENGTH = 60

MIN_PRIORITY = -1000
MAX_PRIORITY = 1000
DEFAULT_PRIORITY = 0

GRACE_PERIOD_SECONDS_ENV_VAR = SKYPILOT_ENV_VAR_PREFIX + 'GRACE_PERIOD_SECONDS'
COST_REPORT_DEFAULT_DAYS = 30

ENV_VAR_LOOP_LAG_THRESHOLD_MS = (SKYPILOT_ENV_VAR_PREFIX +
                                 'DEBUG_LOOP_LAG_THRESHOLD_MS')

ARM64_ARCH = 'arm64'
X86_64_ARCH = 'x86_64'

# Slurm marker file for proctrack type detection.
# Used by the executor to conditionally apply multi-node barrier.
SLURM_PROCTRACK_TYPE_FILE = '.sky_proctrack_type'

SSH_DISABLE_LATENCY_MEASUREMENT_ENV_VAR = (
    f'{SKYPILOT_ENV_VAR_PREFIX}SSH_DISABLE_LATENCY_MEASUREMENT')
