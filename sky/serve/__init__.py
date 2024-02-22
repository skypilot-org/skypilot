"""Modules for SkyServe services."""
import os

from sky.serve.constants import ENDPOINT_PROBE_INTERVAL_SECONDS
from sky.serve.constants import INITIAL_VERSION
from sky.serve.constants import LB_CONTROLLER_SYNC_INTERVAL_SECONDS
from sky.serve.constants import SKYSERVE_METADATA_DIR
from sky.serve.core import down
from sky.serve.core import status
from sky.serve.core import tail_logs
from sky.serve.core import up
from sky.serve.core import update
from sky.serve.serve_state import ReplicaStatus
from sky.serve.serve_state import ServiceStatus
from sky.serve.serve_utils import format_service_table
from sky.serve.serve_utils import generate_replica_cluster_name
from sky.serve.serve_utils import generate_service_name
from sky.serve.serve_utils import get_endpoint
from sky.serve.serve_utils import ServeCodeGen
from sky.serve.serve_utils import ServiceComponent
from sky.serve.serve_utils import SKY_SERVE_CONTROLLER_NAME
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
    'SKY_SERVE_CONTROLLER_NAME',
    'SKYSERVE_METADATA_DIR',
    'status',
    'tail_logs',
    'up',
    'update',
]
