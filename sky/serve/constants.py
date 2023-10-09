"""Constants used for SkyServe."""

import os

CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 10

CONTROLLER_PREFIX = 'sky-serve-controller-'

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SERVE_PREFIX = os.path.expanduser('~/.sky/serve')

# This is the same with sky.skylet.constants.CLUSTER_NAME_VALID_REGEX
# The service name will be used as:
# 1. controller cluster name: 'sky-serve-controller-<service_name>'
# 2. replica cluster name: '<service_name>-<replica_id>'
# In both cases, service name shares the same regex with cluster name.
SERVICE_NAME_VALID_REGEX = '[a-z]([-a-z0-9]*[a-z0-9])?'

CONTROLLER_FILE_LOCK_PATH = f'{SERVE_PREFIX}/controller.lock'
CONTROLLER_FILE_LOCK_TIMEOUT = 20

CONTROLLER_SYNC_INTERVAL = 20
READINESS_PROBE_TIMEOUT = 15

SERVE_STARTUP_TIMEOUT = 60

# We need 200 GB disk space to enable using Azure as controller, since its image
# size is 150 GB. Also, we need 32 GB memory to run our controller and load
# balancer jobs since it is very memory demanding.
# TODO(tian): We might need to be careful that service logs can take a lot of
# disk space. Maybe we could use a larger disk size or migrate to cloud storage.
CONTROLLER_RESOURCES = {'disk_size': 200, 'memory': '32+'}

# Our ray jobs is very memory demanding and number of services on a single
# controller is limited by memory. Rough benchmark result shows each service
# needs ~0.6 GB to run only for controller and load balancer process.
# Considering there will be some sky launch and sky down process on the fly, we
# set the memory usage to 2 GB to be safe.
# In this setup, a default highmem controller with 4 vCPU and 32 GB memory can
# run 16 services.
SERVICES_MEMORY_USAGE_GB = 2.0
SERVICES_TASK_CPU_DEMAND = 0.125

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1

CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30001-31000'
