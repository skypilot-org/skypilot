"""Constants used for SkyServe."""

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SKYSERVE_METADATA_DIR = '~/.sky/serve'

# The filelock for selecting service ports on controller VM when starting a
# service. We need to have a filelock to avoid port collision when starting
# multiple services at the same time.
PORT_SELECTION_FILE_LOCK_PATH = f'{SKYSERVE_METADATA_DIR}/port_selection.lock'

# Signal file path for controller to handle signals.
SIGNAL_FILE_PATH = '/tmp/sky_serve_controller_signal_{}'

# Time to wait in seconds for service to initialize.
INITIALIZATION_TIMEOUT_SECONDS = 60

# The time interval in seconds for load balancer to sync with controller. Every
# time the load balancer syncs with controller, it will update all available
# replica ips for each service, also send the number of requests in last query
# interval.
LB_CONTROLLER_SYNC_INTERVAL_SECONDS = 20

# Interval in seconds to probe replica endpoint.
ENDPOINT_PROBE_INTERVAL_SECONDS = 10

# The default timeout in seconds for a readiness probe request. We set the
# timeout to 15s since using actual generation in LLM services as readiness
# probe is very time-consuming (33B, 70B, ...).
# TODO(tian): Expose this option to users in yaml file.
READINESS_PROBE_TIMEOUT_SECONDS = 15

# Autoscaler window size in seconds for request per second. We calculate rps by
# divide the number of requests in last window size by this window size.
AUTOSCALER_RPS_WINDOW_SIZE_SECONDS = 60
# Autoscaler scale frequency in seconds. We will try to scale up/down every
# `scale_frequency`.
AUTOSCALER_SCALE_FREQUENCY_SECONDS = 20
# Autoscaler cooldown time in seconds. We will not scale up/down if the last
# scale up/down is within this cooldown time.
AUTOSCALER_COOLDOWN_SECONDS = 60

# The default controller resources. We need 200 GB disk space to enable using
# Azure as controller, since its default image size is 150 GB.
# TODO(tian): We might need to be careful that service logs can take a lot of
# disk space. Maybe we could use a larger disk size, migrate to cloud storage or
# do some log rotation.
CONTROLLER_RESOURCES = {'cpus': '4+', 'disk_size': 200}

# A default controller with 4 vCPU and 16 GB memory can run up to 16 services.
SERVICES_MEMORY_USAGE_GB = 1.0
SERVICES_TASK_CPU_DEMAND = 0.25

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1

# Default port range start for controller and load balancer. Ports will be
# automatically generated from this start port.
CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30001-30100'
