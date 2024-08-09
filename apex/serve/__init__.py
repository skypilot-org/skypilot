"""Modules for SkyServe services."""
import os

from apex.serve.constants import ENDPOINT_PROBE_INTERVAL_SECONDS
from apex.serve.constants import INITIAL_VERSION
from apex.serve.constants import LB_CONTROLLER_SYNC_INTERVAL_SECONDS
from apex.serve.constants import SKYSERVE_METADATA_DIR
from apex.serve.core import down
from apex.serve.core import status
from apex.serve.core import tail_logs
from apex.serve.core import up
from apex.serve.core import update
from apex.serve.serve_state import ReplicaStatus
from apex.serve.serve_state import ServiceStatus
from apex.serve.serve_utils import DEFAULT_UPDATE_MODE
from apex.serve.serve_utils import format_service_table
from apex.serve.serve_utils import generate_replica_cluster_name
from apex.serve.serve_utils import generate_service_name
from apex.serve.serve_utils import get_endpoint
from apex.serve.serve_utils import ServeCodeGen
from apex.serve.serve_utils import ServiceComponent
from apex.serve.serve_utils import SKY_SERVE_CONTROLLER_NAME
from apex.serve.serve_utils import UpdateMode
from apex.serve.service_spec import SkyServiceSpec

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
    'UpdateMode',
    'DEFAULT_UPDATE_MODE',
]
