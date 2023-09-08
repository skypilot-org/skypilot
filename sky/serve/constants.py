"""Constants used for SkyServe."""

CONTROLLER_PREFIX = 'sky-serve-controller-'

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SERVE_PREFIX = '~/.sky/serve'

# This is the same with sky.skylet.constants.CLUSTER_NAME_VALID_REGEX
# The service name will be used as:
# 1. controller cluster name: 'sky-serve-controller-<service_name>'
# 2. replica cluster name: '<service_name>-<replica_id>'
# In both cases, service name shares the same regex with cluster name.
SERVICE_NAME_VALID_REGEX = '[a-z]([-a-z0-9]*[a-z0-9])?'

CONTROLLER_FILE_LOCK_PATH = (f'{SERVE_PREFIX}/controller.lock')
CONTROLLER_FILE_LOCK_TIMEOUT = 20

CONTROLLER_SYNC_INTERVAL = 20
READINESS_PROBE_TIMEOUT = 15

SERVE_STARTUP_TIMEOUT = 60

# We need 200GiB disk space to enable using Azure as controller, since its image
# size is 150GiB. Also, we need 32 GiB memory to run our controller and load
# balancer jobs since it is very memory demanding.
CONTROLLER_RESOURCES = {'disk_size': 200, 'memory': '32+'}
# Our ray jobs is very memory demanding and number of services on a single
# controller is limited by memory. Rough benchmark result shows each service
# needs ~1GB to run, we set the memory usage to 2GB to avoid OOM.
# TODO(tian): Change ray job resources requirements to small number of vCPU
# (like 0.125) per job.
SERVICES_MEMORY_USAGE_GB = 0.75

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1

CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30001-31000'
