"""Constants used for SkyServe."""

CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 10

# A prefix for all controller clusters. We use this prefix to identify a
# skyserve controller cluster. We will append a user hash and an incremental
# id to this prefix to generate a unique controller cluster name every time.
CONTROLLER_PREFIX = 'sky-serve-controller-'

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SERVE_PREFIX = '~/.sky/serve'

# The filelock for selecting controller and service ports when starting a
# service. In our current multi-service controller implementation, we need to
# select a controller if there are some existing controllers.
CONTROLLER_FILE_LOCK_PATH = f'{SERVE_PREFIX}/controller.lock'

# The filelock for selecting service ports when starting a service. We need to
# have a filelock to avoid port collision when starting multiple services at
# the same time.
PORT_SELECTION_FILE_LOCK_PATH = f'{SERVE_PREFIX}/port_selection.lock'

# Signal file path for controller to handle signals.
SIGNAL_FILE_PATH = '/tmp/sky_serve_controller_signal_{}'

# Timeout for `sky serve down`.
SERVICE_TERMINATION_TIMEOUT = 180

# Timeout for waiting controller to find a port for service processes.
SERVICE_PORT_SELECTION_TIMEOUT = 60

# The time interval for load balancer to sync with controller. Every time the
# load balancer syncs with controller, it will update all available replica ips
# for each service, also send the number of requests in last query interval.
LB_CONTROLLER_SYNC_INTERVAL = 20

# Interval to probe replica endpoint.
ENDPOINT_PROBE_INTERVAL = 10

# The default timeout for a readiness probe request. We set the timeout to 15s
# since using actual generation in LLM services as readiness probe is very
# time-consuming (33B, 70B, ...).
# TODO(tian): Expose this option to users in yaml file.
READINESS_PROBE_TIMEOUT = 15

# Wait for 1 minutes for controller / load balancer to terminate.
SERVE_TERMINATE_WAIT_TIMEOUT = 60

# Autoscaler window size for request per second. We calculate rps by divide the
# number of requests in last window size by this window size.
AUTOSCALER_RPS_WINDOW_SIZE = 60
# Autoscaler scale frequency. We will try to scale up/down every
# `scale_frequency`.
AUTOSCALER_SCALE_FREQUENCY = 20
# Autoscaler cooldown time. We will not scale up/down if the last scale up/down
# is within this cooldown time.
AUTOSCALER_COOLDOWN_SECONDS = 60

# The default controller resources.
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

# Default port range start for controller and load balancer. Ports will be
# automatically generated from this start port.
CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30001-30100'
