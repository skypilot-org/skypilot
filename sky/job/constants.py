"""Constants used for Managed Jobs."""

JOB_CONTROLLER_TEMPLATE = 'job-controller.yaml.j2'
JOB_CONTROLLER_YAML_PREFIX = '~/.sky/job_controller'

JOB_TASK_YAML_PREFIX = '~/.sky/managed_jobs'

# Resources as a dict for the job controller.
# Use default CPU instance type for job controller with >= 24GB, i.e.
# m6i.2xlarge (8vCPUs, 32 GB) for AWS, Standard_D8s_v4 (8vCPUs, 32 GB)
# for Azure, and n1-standard-8 (8 vCPUs, 32 GB) for GCP, etc.
# Based on profiling, memory should be at least 3x (in GB) as num vCPUs to avoid
# OOM (each vCPU can have 4 job controller processes as we set the CPU
# requirement to 0.25, and 3 GB is barely enough for 4 spot processes).
# We use 50 GB disk size to reduce the cost.
CONTROLLER_RESOURCES = {'cpus': '8+', 'memory': '3x', 'disk_size': 50}

# Max length of the cluster name for GCP is 35, the user hash to be attached is
# 4+1 chars, and we assume the maximum length of the job id is 4+1, so the max
# length of the cluster name prefix is 25 to avoid the cluster name being too
# long and truncated twice during the cluster creation.
JOB_CLUSTER_NAME_PREFIX_LENGTH = 25

# The version of the lib files that job/utils use. Whenever there is an API
# change for the job/utils, we need to bump this version, so that the
# user can be notified to update their SkyPilot version on the remote cluster.
MANAGED_LIB_VERSION = 1
