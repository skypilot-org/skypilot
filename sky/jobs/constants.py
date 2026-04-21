"""Constants used for Managed Jobs."""
import os
from typing import Any, Dict, Union

# Environment variable for JobGroup name, injected into all jobs in a JobGroup
SKYPILOT_JOBGROUP_NAME_ENV_VAR = 'SKYPILOT_JOBGROUP_NAME'

JOBS_CONTROLLER_TEMPLATE = 'jobs-controller.yaml.j2'
JOBS_CONTROLLER_PROVISION_TEMPLATE = 'jobs-controller-provision.yaml.j2'
JOBS_CONTROLLER_YAML_PREFIX = '~/.sky/jobs_controller'
JOBS_CONTROLLER_LOGS_DIR = '~/sky_logs/jobs_controller'

JOBS_TASK_YAML_PREFIX = '~/.sky/managed_jobs'

JOB_CONTROLLER_INDICATOR_FILE = '~/.sky/is_jobs_controller'

CONSOLIDATED_SIGNAL_PATH = os.path.expanduser('~/.sky/signals/')
SIGNAL_FILE_PREFIX = '/tmp/sky_jobs_controller_signal_{}'

# The consolidation mode lock ensures that if multiple API servers are running
# at the same time (e.g. during a rolling update), recovery can only happen once
# the previous API server has exited.
CONSOLIDATION_MODE_LOCK_ID = '~/.sky/consolidation_mode_lock'

# Signal file indicating the API server has been restarted after enabling
# consolidation mode. Written by setup_consolidation_mode_on_startup() in
# sky/jobs/utils.py. It is the single source of truth for jobs-controller
# consolidation state and is read via the helpers in
# sky/utils/controller_utils.py:
#   - is_jobs_consolidation_mode() — user-facing reader. Shared by both
#     sky/jobs/utils.py::is_consolidation_mode() (managed jobs) and
#     sky/serve/serve_utils.py::is_consolidation_mode(pool=True) (pools),
#     which are thin wrappers. Pool and managed-jobs readers route through
#     the same helper so they cannot diverge.
#   - _is_consolidation_mode(pool=True) — sizing-only helper.
# Reading config directly instead diverges under deploy-mode auto-enable
# (config stays null while this file is written).
JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE = (
    '~/.sky/.jobs_controller_consolidation_reloaded_signal')

# Resources as a dict for the jobs controller.
# We use 50 GB disk size to reduce the cost.
CONTROLLER_RESOURCES: Dict[str, Union[str, int]] = {
    'cpus': '4+',
    'memory': '4x',
    'disk_size': 50
}

# Autostop config for the jobs controller. These are the default values for
# jobs.controller.autostop in ~/.sky/config.yaml.
CONTROLLER_AUTOSTOP: Dict[str, Any] = {
    'idle_minutes': 10,
    'down': False,
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
# WARNING: If you update this due to a codegen change, make sure to make the
# corresponding change in the ManagedJobsService AND bump the SKYLET_VERSION.
MANAGED_JOBS_VERSION = 18  # batch fields (is_batch, batch_total_batches, etc.)
