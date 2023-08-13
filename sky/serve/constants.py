"""Constants used for SkyServe."""

CONTROLLER_PREFIX = 'controller-'

CONTROLLER_TEMPLATE = 'skyserve-controller.yaml.j2'

SERVE_PREFIX = '~/.sky/serve'

CONTROL_PLANE_PORT = 31001
CONTROL_PLANE_SYNC_INTERVAL = 20

CONTROLLER_RESOURCES = {'disk_size': 100, 'cpus': '4+'}

# A period of time to initialize your service. Any readiness probe failures
# during this period will be ignored.
DEFAULT_INITIAL_DELAY_SECONDS = 1200
DEFAULT_MIN_REPLICAS = 1
