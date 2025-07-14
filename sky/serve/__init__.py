"""Modules for SkyServe services."""
import os

from sky.serve.client.sdk import down
from sky.serve.client.sdk import status
from sky.serve.client.sdk import sync_down_logs
from sky.serve.client.sdk import tail_logs
from sky.serve.client.sdk import terminate_replica
from sky.serve.client.sdk import up
from sky.serve.client.sdk import update
from sky.serve.constants import ENDPOINT_PROBE_INTERVAL_SECONDS
from sky.serve.constants import INITIAL_VERSION
from sky.serve.constants import LB_CONTROLLER_SYNC_INTERVAL_SECONDS
from sky.serve.constants import SKYSERVE_METADATA_DIR
from sky.serve.load_balancing_policies import LB_POLICIES
from sky.serve.serve_state import ReplicaStatus
from sky.serve.serve_state import ServiceStatus
from sky.serve.serve_utils import DEFAULT_UPDATE_MODE
from sky.serve.serve_utils import format_service_table
from sky.serve.serve_utils import generate_replica_cluster_name
from sky.serve.serve_utils import generate_service_name
from sky.serve.serve_utils import ServeCodeGen
from sky.serve.serve_utils import ServiceComponent
from sky.serve.serve_utils import UpdateMode
from sky.serve.serve_utils import validate_service_task
from sky.serve.service_spec import SkyServiceSpec

os.makedirs(os.path.expanduser(SKYSERVE_METADATA_DIR), exist_ok=True)

__all__ = [
    'down',
    'ENDPOINT_PROBE_INTERVAL_SECONDS',
    'format_service_table',
    'generate_replica_cluster_name',
    'generate_service_name',
    'INITIAL_VERSION',
    'LB_CONTROLLER_SYNC_INTERVAL_SECONDS',
    'LB_POLICIES',
    'ReplicaStatus',
    'ServiceComponent',
    'sync_down_logs',
    'ServiceStatus',
    'ServeCodeGen',
    'SkyServiceSpec',
    'SKYSERVE_METADATA_DIR',
    'status',
    'terminate_replica',
    'tail_logs',
    'up',
    'update',
    'UpdateMode',
    'DEFAULT_UPDATE_MODE',
]
