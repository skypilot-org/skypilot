"""Constants used for SkyServe."""

CONTROLLER_PREFIX = 'sky-serve-controller-'

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SERVE_PREFIX = '~/.sky/serve'

PORTS_GENERATION_FILE_LOCK_PATH = f'{SERVE_PREFIX}/ports.lock'
PORTS_GENERATION_FILE_LOCK_TIMEOUT = 20
CONTROLLER_SELECTION_FILE_LOCK_PATH = (
    f'{SERVE_PREFIX}/controller_selection.lock')
CONTROLLER_SELECTION_FILE_LOCK_TIMEOUT = 30

CONTROLLER_SYNC_INTERVAL = 20

SERVE_STARTUP_TIMEOUT = 60

# We need 200GB disk space to enable using Azure as controller, since its image
# size is 150GB.
CONTROLLER_RESOURCES = {'disk_size': 200, 'cpus': '4+'}
# Our ray jobs is very memory demanding and number of services on a single
# controller is limited by memory. Rough benchmark result shows each service
# needs ~1GB to run, we set the memory usage to 2GB to avoid OOM.
# TODO(tian): Change ray job resources requirements to 0.25 vCPU per job.
# TODO(tian): Change to 1.5
SERVICES_MEMORY_USAGE_GB = 8

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1

CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30000-31000'
