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

SKYLET_VERSION = '2'
SKYLET_VERSION_FILE = '~/.sky/skylet_version'

# `sky spot dashboard`-related
#
# Port on the remote spot controller that the dashboard is running on.
SPOT_DASHBOARD_REMOTE_PORT = 5000

# Install conda on the remote cluster if it is not already installed.
# We do not install the latest conda with python 3.11 because ray has not
# officially supported it yet.
# https://github.com/ray-project/ray/issues/31606
CONDA_INSTALLATION_COMMANDS = (
    '(which conda > /dev/null 2>&1 && conda init > /dev/null) || '
    '(wget -nc https://repo.anaconda.com/miniconda/Miniconda3-py39_23.5.2-0-Linux-x86_64.sh -O Miniconda3-Linux-x86_64.sh && '  # pylint: disable=line-too-long
    'bash Miniconda3-Linux-x86_64.sh -b && '
    'eval "$(~/miniconda3/bin/conda shell.bash hook)" && conda init && '
    'conda config --set auto_activate_base true);')
