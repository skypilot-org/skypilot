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

# Default fields returned by the managed jobs queue when the caller does not
# specify `fields`. This intentionally excludes heavy fields (e.g. the task
# YAML) so the default queue payload stays small even with many jobs. Callers
# that need every field must pass `fields=None` explicitly.
# Defined as a tuple so it is immutable and safe to use as a default argument.
DEFAULT_MANAGED_JOB_FIELDS = ('job_id', 'task_id', 'workspace', 'job_name',
                              'task_name', 'resources', 'submitted_at',
                              'end_at', 'job_duration', 'recovery_count',
                              'status', 'pool', 'is_primary_in_job_group',
                              'batch_total_batches', 'batch_completed_batches')

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
MANAGED_JOBS_VERSION = 22  # add submitted_after/submitted_before to job table

# Emergency recovery: when the job controller hits an unexpected internal
# error (e.g. external mutation of the job state, or an unhandled exception
# in the controller's job loop), it retries managing the job in place
# instead of failing the job terminally. These constants bound that retry.
#
# Max attempts in one episode before giving up and marking the job
# FAILED_CONTROLLER (with full resource cleanup).
EMERGENCY_RECOVERY_MAX_ATTEMPTS = 10
# Nominal backoff before attempt N is
# min(BASE * 2^(N-1), CAP) = 1m, 2m, 4m, 8m, 16m, then 30m (capped) —
# ~3h of total backoff across a full episode. The actual sleep is jittered
# +/-50% around this nominal value (so the average is unchanged): a systemic
# incident pushes many jobs into emergency recovery at once, and a
# deterministic backoff would resynchronize their retries into DB-load waves
# (worst under consolidation mode's shared DB). The controller logs both the
# nominal and the jittered sleep. The backoff may exceed the
# jobs-controller's 10-minute idle autostop window: that is safe because
# the job's schedule_state stays ALIVE throughout the backoff (the
# emergency bookkeeping resets launch-adjacent states back to ALIVE, which
# also keeps the job out of the scheduler's blocking-priority set), and
# the skylet autostop check (sky/skylet/events.py::AutostopEvent, via
# managed_job_state.get_num_alive_jobs) does not consider the controller
# idle while any such job exists. Nor does a backing-off job hold one of
# the LAUNCHES_PER_WORKER launching slots (the in-memory `starting` set):
# scheduled_launch's finally releases the slot only when the error escapes
# from inside the launch context, so the emergency bookkeeping explicitly
# discards the job from `starting` (JobController._release_launch_slot) to
# cover the cases it does not — an error in pre/post-launch bookkeeping, or
# a pool job (which never enters scheduled_launch's slot accounting). The
# retry re-adds it when it actually relaunches. (The job's own cluster may
# be reaped by the 10-minute autodown backstop during a long backoff; that
# is fine — the retry always relaunches from scratch.)
EMERGENCY_RECOVERY_BACKOFF_BASE_SECONDS = 60
EMERGENCY_RECOVERY_BACKOFF_CAP_SECONDS = 30 * 60
# If the previous emergency recovery attempt is older than this window, the
# attempt counter restarts at 1: a long-running job that hits a rare
# incident every few days should recover every time, while a tight crash
# loop exhausts the budget in a few hours. A successful recovery does NOT
# reset the counter — only an emergency-free gap longer than this window
# does. An error that keeps recurring inside the window (even with healthy
# runs in between) is deliberately charged as one escalating episode: a
# recovery alone doesn't prove the underlying problem is gone, and
# resetting on success would let a recurring-but-recoverable error relaunch
# the cluster forever.
EMERGENCY_RECOVERY_RESET_WINDOW_SECONDS = 6 * 60 * 60

# Prefix used for service-account tokens issued to managed jobs that opt in
# to api_server_access. The expired-token-cleanup daemon uses this prefix to
# identify managed-job tokens that should be swept once their TTL passes.
# Keep this in sync with the token name format in
# sky/jobs/server/core.py::_create_job_api_token.
MANAGED_JOB_TOKEN_NAME_PREFIX = 'managed-job-'

# TTL for service-account tokens issued to managed jobs with
# api_server_access. Kept short so any tokens that leak past the controller
# cleanup are reaped quickly by the expired-token-cleanup daemon.
# TODO(lloyd-brown): The controller does not renew this token while the job is
# still running, so long-running jobs (e.g. multi-day training) can have their
# api_server_access token expire mid-run. Add token renewal so the TTL only
# bounds leaked-token lifetime, not in-use token lifetime.
MANAGED_JOB_TOKEN_TTL_DAYS = 3
