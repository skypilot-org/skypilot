"""Modules for SkyServe services."""
import os

from sky.serve.constants import CONTROLLER_RESOURCES
from sky.serve.constants import CONTROLLER_TEMPLATE
from sky.serve.constants import ENDPOINT_PROBE_INTERVAL_SECONDS
from sky.serve.constants import LB_CONTROLLER_SYNC_INTERVAL_SECONDS
from sky.serve.constants import LOAD_BALANCER_PORT_RANGE
from sky.serve.constants import SERVICES_TASK_CPU_DEMAND
from sky.serve.constants import SKYSERVE_METADATA_DIR
from sky.serve.serve_state import ReplicaStatus
from sky.serve.serve_state import ServiceStatus
from sky.serve.serve_utils import generate_remote_config_yaml_file_name
from sky.serve.serve_utils import generate_remote_controller_log_file_name
from sky.serve.serve_utils import generate_remote_task_yaml_file_name
from sky.serve.serve_utils import generate_replica_cluster_name
from sky.serve.serve_utils import generate_service_name
from sky.serve.serve_utils import load_add_service_result
from sky.serve.serve_utils import load_serve_status
from sky.serve.serve_utils import ServeCodeGen
from sky.serve.serve_utils import ServiceComponent
from sky.serve.serve_utils import SKY_SERVE_CONTROLLER_NAME
from sky.serve.service_spec import SkyServiceSpec

os.makedirs(os.path.expanduser(SKYSERVE_METADATA_DIR), exist_ok=True)
