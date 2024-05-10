"""Constants used for Managed Jobs."""

JOBS_CONTROLLER_TEMPLATE = 'jobs-controller.yaml.j2'
JOBS_CONTROLLER_YAML_PREFIX = '~/.sky/jobs_controller'

JOBS_TASK_YAML_PREFIX = '~/.sky/managed_jobs'

# Resources as a dict for the jobs controller.
# Use default CPU instance type for jobs controller with >= 24GB, i.e.
# m6i.2xlarge (8vCPUs, 32 GB) for AWS, Standard_D8s_v4 (8vCPUs, 32 GB)
# for Azure, and n1-standard-8 (8 vCPUs, 32 GB) for GCP, etc.
# Based on profiling, memory should be at least 3x (in GB) as num vCPUs to avoid
# OOM (each vCPU can have 4 jobs controller processes as we set the CPU
# requirement to 0.25, and 3 GB is barely enough for 4 job processes).
# We use 50 GB disk size to reduce the cost.
CONTROLLER_RESOURCES = {'cpus': '8+', 'memory': '3x', 'disk_size': 50}

# Max length of the cluster name for GCP is 35, the user hash to be attached is
# 4+1 chars, and we assume the maximum length of the job id is 4+1, so the max
# length of the cluster name prefix is 25 to avoid the cluster name being too
# long and truncated twice during the cluster creation.
JOBS_CLUSTER_NAME_PREFIX_LENGTH = 25

# The version of the lib files that jobs/utils use. Whenever there is an API
# change for the jobs/utils, we need to bump this version and update
# job.utils.ManagedJobCodeGen to handle the version update.
MANAGED_JOBS_VERSION = 1
