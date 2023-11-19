"""Constants used for Managed Spot."""

SPOT_CONTROLLER_TEMPLATE = 'spot-controller.yaml.j2'
SPOT_CONTROLLER_YAML_PREFIX = '~/.sky/spot_controller'

SPOT_TASK_YAML_PREFIX = '~/.sky/spot_tasks'

# Resources as a dict for the spot controller.
# Use default CPU instance type for spot controller, i.e.
# m6i.2xlarge (8vCPUs, 32 GB) for AWS, Standard_D8s_v4 (8vCPUs, 32 GB)
# for Azure, and n1-standard-8 (8 vCPUs, 32 GB) for GCP.
# We use 50 GB disk size to reduce the cost.
CONTROLLER_RESOURCES = {'disk_size': 50}

# Max length of the cluster name for GCP is 35, the user hash to be attached is
# 4+1 chars, and we assume the maximum length of the job id is 4+1, so the max
# length of the cluster name prefix is 25 to avoid the cluster name being too
# long and truncated twice during the cluster creation.
SPOT_CLUSTER_NAME_PREFIX_LENGTH = 25
