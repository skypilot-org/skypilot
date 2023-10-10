"""Modules for SkyServe services."""
import os

from sky.serve.constants import CONTROLLER_FILE_LOCK_PATH
from sky.serve.constants import CONTROLLER_FILE_LOCK_TIMEOUT
from sky.serve.constants import CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP
from sky.serve.constants import CONTROLLER_PREFIX
from sky.serve.constants import CONTROLLER_RESOURCES
from sky.serve.constants import CONTROLLER_SYNC_INTERVAL
from sky.serve.constants import CONTROLLER_TEMPLATE
from sky.serve.constants import SERVE_PREFIX
from sky.serve.constants import SERVICES_TASK_CPU_DEMAND
from sky.serve.serve_utils import gen_ports_for_serve_process
from sky.serve.serve_utils import generate_controller_yaml_file_name
from sky.serve.serve_utils import generate_remote_controller_log_file_name
from sky.serve.serve_utils import generate_remote_load_balancer_log_file_name
from sky.serve.serve_utils import generate_remote_service_dir_name
from sky.serve.serve_utils import generate_remote_task_yaml_file_name
from sky.serve.serve_utils import generate_replica_cluster_name
from sky.serve.serve_utils import get_available_controller_name
from sky.serve.serve_utils import load_latest_info
from sky.serve.serve_utils import load_terminate_service_result
from sky.serve.serve_utils import ServeCodeGen
from sky.serve.serve_utils import ServiceComponent
from sky.serve.serve_utils import ServiceHandle
from sky.serve.service_spec import SkyServiceSpec

os.makedirs(os.path.expanduser(SERVE_PREFIX), exist_ok=True)
