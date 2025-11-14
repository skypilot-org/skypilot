"""Constants used for SkyServe."""

CONTROLLER_TEMPLATE = 'sky-serve-controller.yaml.j2'

SKYSERVE_METADATA_DIR = '~/.sky/serve'

# The filelock for selecting service ports on controller VM when starting a
# service. We need to have a filelock to avoid port collision when starting
# multiple services at the same time.
PORT_SELECTION_FILE_LOCK_PATH = f'{SKYSERVE_METADATA_DIR}/port_selection.lock'

# Signal file path for controller to handle signals.
SIGNAL_FILE_PATH = '/tmp/sky_serve_controller_signal_{}'

# Time to wait in seconds for controller to setup, this involves the time to run
# cloud dependencies installation.
CONTROLLER_SETUP_TIMEOUT_SECONDS = 300
# Time to wait in seconds for service to register on the controller.
SERVICE_REGISTER_TIMEOUT_SECONDS = 60

# The time interval in seconds for load balancer to sync with controller. Every
# time the load balancer syncs with controller, it will update all available
# replica ips for each service, also send the number of requests in last query
# interval.
LB_CONTROLLER_SYNC_INTERVAL_SECONDS = 20

# The maximum retry times for load balancer for each request. After changing to
# proxy implementation, we do retry for failed requests.
# TODO(tian): Expose this option to users in yaml file.
LB_MAX_RETRY = 3

# The timeout in seconds for load balancer to wait for a response from replica.
# Large LLMs like Llama2-70b is able to process the request within ~30 seconds.
# We set the timeout to 120s to be safe. For reference, FastChat uses 100s:
# https://github.com/lm-sys/FastChat/blob/f2e6ca964af7ad0585cadcf16ab98e57297e2133/fastchat/constants.py#L39 # pylint: disable=line-too-long
# TODO(tian): Expose this option to users in yaml file.
LB_STREAM_TIMEOUT = 120

# Interval in seconds to probe replica endpoint.
ENDPOINT_PROBE_INTERVAL_SECONDS = 10

# The default timeout in seconds for a readiness probe request. We set the
# timeout to 15s since using actual generation in LLM services as readiness
# probe is very time-consuming (33B, 70B, ...).
DEFAULT_READINESS_PROBE_TIMEOUT_SECONDS = 15

# Autoscaler window size in seconds for query per second. We calculate qps by
# divide the number of queries in last window size by this window size.
AUTOSCALER_QPS_WINDOW_SIZE_SECONDS = 60
# Autoscaler scale decision interval in seconds.
# We will try to scale up/down every `decision_interval`.
AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS = 20
# Autoscaler no replica decision interval in seconds.
AUTOSCALER_NO_REPLICA_DECISION_INTERVAL_SECONDS = 5
# Autoscaler default upscale delays in seconds.
# We will upscale only if the target number of instances
# is larger than the current launched instances for delay amount of time.
AUTOSCALER_DEFAULT_UPSCALE_DELAY_SECONDS = 300
# Autoscaler default downscale delays in seconds.
# We will downscale only if the target number of instances
# is smaller than the current launched instances for delay amount of time.
AUTOSCALER_DEFAULT_DOWNSCALE_DELAY_SECONDS = 1200
# The default controller resources. We need 200 GB disk space to enable using
# Azure as controller, since its default image size is 150 GB.
# TODO(tian): We might need to be careful that service logs can take a lot of
# disk space. Maybe we could use a larger disk size, migrate to cloud storage or
# do some log rotation.
CONTROLLER_RESOURCES = {'cpus': '4+', 'disk_size': 200}
# Autostop config for the jobs controller. These are the default values for
# serve.controller.autostop in ~/.sky/config.yaml.
CONTROLLER_AUTOSTOP = {
    'idle_minutes': 10,
    'down': False,
}

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1

# Default port range start for controller and load balancer. Ports will be
# automatically generated from this start port.
CONTROLLER_PORT_START = 20001
LOAD_BALANCER_PORT_START = 30001
LOAD_BALANCER_PORT_RANGE = '30001-30020'

# Initial version of service.
INITIAL_VERSION = 1

# Replica ID environment variable name that can be accessed on the replica.
REPLICA_ID_ENV_VAR = 'SKYPILOT_SERVE_REPLICA_ID'

# The version of the lib files that serve use. Whenever there is an API
# change for the serve_utils.ServeCodeGen, we need to bump this version, so that
# the user can be notified to update their SkyPilot serve version on the remote
# cluster.
# Changelog:
# v1.0 - Introduce rolling update.
# v2.0 - Added template-replica feature.
# v3.0 - Added pool.
# v4.0 - Added pool argument to wait_service_registration.
# v5.0 - Added pool argument to stream_serve_process_logs & stream_replica_logs.
SERVE_VERSION = 5

TERMINATE_REPLICA_VERSION_MISMATCH_ERROR = (
    'The version of service is outdated and does not support manually '
    'terminating replicas. Please terminate the service and spin up again.')

# Dummy run command for pool.
POOL_DUMMY_RUN_COMMAND = 'echo "setup done"'

# Error message for max number of services reached.
MAX_NUMBER_OF_SERVICES_REACHED_ERROR = 'Max number of services reached.'
