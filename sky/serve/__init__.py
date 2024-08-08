"""Modules for SkyServe services."""
import os

from sky.serve.api.sdk import down
from sky.serve.api.sdk import status
from sky.serve.api.sdk import tail_logs
from sky.serve.api.sdk import up
from sky.serve.api.sdk import update
from sky.serve.constants import ENDPOINT_PROBE_INTERVAL_SECONDS
from sky.serve.constants import INITIAL_VERSION
from sky.serve.constants import LB_CONTROLLER_SYNC_INTERVAL_SECONDS
from sky.serve.constants import SKYSERVE_METADATA_DIR
from sky.serve.serve_state import ReplicaStatus
from sky.serve.serve_state import ServiceStatus
from sky.serve.serve_utils import DEFAULT_UPDATE_MODE
from sky.serve.serve_utils import format_service_table
from sky.serve.serve_utils import generate_replica_cluster_name
from sky.serve.serve_utils import generate_service_name
from sky.serve.serve_utils import get_endpoint
from sky.serve.serve_utils import ServeCodeGen
from sky.serve.serve_utils import ServiceComponent
from sky.serve.serve_utils import UpdateMode
from sky.serve.service_spec import SkyServiceSpec

os.makedirs(os.path.expanduser(SKYSERVE_METADATA_DIR), exist_ok=True)

__all__ = [
    'down',
    'ENDPOINT_PROBE_INTERVAL_SECONDS',
    'format_service_table',
    'generate_replica_cluster_name',
    'generate_service_name',
    'get_endpoint',
    'INITIAL_VERSION',
    'LB_CONTROLLER_SYNC_INTERVAL_SECONDS',
    'ReplicaStatus',
    'ServiceComponent',
    'ServiceStatus',
    'ServeCodeGen',
    'SkyServiceSpec',
    'SKYSERVE_METADATA_DIR',
    'status',
    'tail_logs',
    'up',
    'update',
    'UpdateMode',
    'DEFAULT_UPDATE_MODE',
]
