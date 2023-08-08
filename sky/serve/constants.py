"""Constants used for SkyServe."""

CONTROLLER_PREFIX = 'controller-'

CONTROLLER_TEMPLATE = 'skyserve-controller.yaml.j2'
CONTROLLER_YAML_PREFIX = '~/.sky/serve'

SERVICE_YAML_PREFIX = '~/.sky/service'

CONTROL_PLANE_PORT = 31001
CONTROL_PLANE_SYNC_INTERVAL = 20

# TODO(tian): Remove cloud == GCP.
CONTROLLER_RESOURCES = {'cloud': 'gcp', 'disk_size': 100, 'cpus': 4}
