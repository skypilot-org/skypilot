"""Constants used for Managed Jobs."""
from typing import Dict, Union

from sky.skylet import constants as skylet_constants

JOBS_CONTROLLER_TEMPLATE = 'jobs-controller.yaml.j2'
JOBS_CONTROLLER_YAML_PREFIX = '~/.sky/jobs_controller'
JOBS_CONTROLLER_LOGS_DIR = '~/sky_logs/jobs_controller'

JOBS_TASK_YAML_PREFIX = '~/.sky/managed_jobs'

# Resources as a dict for the jobs controller.
# Use smaller CPU instance type for jobs controller, but with more memory, i.e.
# r6i.xlarge (4vCPUs, 32 GB) for AWS, Standard_E4s_v5 (4vCPUs, 32 GB) for Azure,
# and n2-highmem-4 (4 vCPUs, 32 GB) for GCP, etc.
# Concurrently limits are set based on profiling. 4x num vCPUs is the launch
# parallelism limit, and memory / 350MB is the limit to concurrently running
# jobs. See _get_launch_parallelism and _get_job_parallelism in scheduler.py.
# We use 50 GB disk size to reduce the cost.
CONTROLLER_RESOURCES: Dict[str, Union[str, int]] = {
    'cpus': '4+',
    'memory': '8x',
    'disk_size': 50
}

# TODO(zhwu): This is no longer accurate, after #4592, which increases the
# length of user hash appended to the cluster name from 4 to 8 chars. This makes
# the cluster name on GCP being wrapped twice. However, we cannot directly
# update this constant, because the job cluster cleanup and many other logic
# in managed jobs depends on this constant, i.e., updating this constant will
# break backward compatibility and existing jobs.
#
# Max length of the cluster name for GCP is 35, the user hash to be attached is
# 4(now 8)+1 chars, and we assume the maximum length of the job id is
# 4(now 8)+1, so the max length of the cluster name prefix is 25(should be 21
# now) to avoid the cluster name being too long and truncated twice during the
# cluster creation.
JOBS_CLUSTER_NAME_PREFIX_LENGTH = 25

# The version of the lib files that jobs/utils use. Whenever there is an API
# change for the jobs/utils, we need to bump this version and update
# job.utils.ManagedJobCodeGen to handle the version update.
MANAGED_JOBS_VERSION = 3

# The command for setting up the jobs dashboard on the controller. It firstly
# checks if the systemd services are available, and if not (e.g., Kubernetes
# containers may not have systemd), it starts the dashboard manually.
DASHBOARD_SETUP_CMD = (
    'if command -v systemctl &>/dev/null && systemctl --user show &>/dev/null; '
    'then '
    '  systemctl --user daemon-reload; '
    '  systemctl --user enable --now skypilot-dashboard; '
    'else '
    '  echo "Systemd services not found. Starting SkyPilot dashboard '
    'manually."; '
    # Kill any old dashboard processes;
    '  ps aux | grep -v nohup | grep -v grep | '
    '  grep -- \'-m sky.jobs.dashboard.dashboard\' | awk \'{print $2}\' | '
    '  xargs kill > /dev/null 2>&1 || true;'
    # Launch the dashboard in the background if not already running
    '  (ps aux | grep -v nohup | grep -v grep | '
    '  grep -q -- \'-m sky.jobs.dashboard.dashboard\') || '
    f'(nohup {skylet_constants.SKY_PYTHON_CMD} -m sky.jobs.dashboard.dashboard '
    '>> ~/.sky/job-dashboard.log 2>&1 &); '
    'fi')
