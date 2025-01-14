"""Constants used for Managed Jobs."""

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
CONTROLLER_RESOURCES = {'cpus': '4+', 'memory': '8x', 'disk_size': 50}

# Max length of the cluster name for GCP is 35, the user hash to be attached is
# 4+1 chars, and we assume the maximum length of the job id is 4+1, so the max
# length of the cluster name prefix is 25 to avoid the cluster name being too
# long and truncated twice during the cluster creation.
JOBS_CLUSTER_NAME_PREFIX_LENGTH = 25

# The version of the lib files that jobs/utils use. Whenever there is an API
# change for the jobs/utils, we need to bump this version and update
# job.utils.ManagedJobCodeGen to handle the version update.
MANAGED_JOBS_VERSION = 1
