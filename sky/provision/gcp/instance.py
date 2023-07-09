"""GCP instance provisioning."""
import time
from typing import Dict, List, Any, Optional

from sky import sky_logging
from sky.adaptors import gcp
from sky.provision.gcp import instance_utils

logger = sky_logging.init_logger(__name__)

MAX_POLLS = 12
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_STOP = MAX_POLLS * 8
POLL_INTERVAL = 5

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'

_NEED_TO_STOP_STATES = [
    'PROVISIONING',
    'STAGING',
    'RUNNING',
]
_NON_STOPPED_STATES = _NEED_TO_STOP_STATES + ['STOPPING']

_NEED_TO_TERMINATE_STATES = _NON_STOPPED_STATES + [
    'TERMINATED',  # Stopped instances are in this state
]


def _wait_for_operations(
    instance_handler,
    operations: List[dict],
    project_id: str,
    zone: str,
) -> None:
    """Poll for compute zone operation until finished."""
    total_polls = 0
    for operation in operations:
        logger.debug('wait_for_compute_zone_operation: '
                     f'Waiting for operation {operation["name"]} to finish...')
        while total_polls < MAX_POLLS:
            if instance_handler.wait_for_operation(project_id, zone, operation):
                break
            time.sleep(POLL_INTERVAL)
            total_polls += 1


def stop_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> None:
    assert provider_config is not None, cluster_name
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    instance_handler = instance_utils.GCPComputeInstance
    name_filter = {TAG_RAY_CLUSTER_NAME: cluster_name}
    instances = instance_handler.filter(project_id, zone, name_filter,
                                        _NEED_TO_STOP_STATES,
                                        included_instances, excluded_instances)
    operations = []
    for instance in instances:
        operations.append(instance_handler.stop(project_id, zone, instance))
    _wait_for_operations(instance_handler, operations, project_id, zone)
    # Check if the instance is actually stopped.
    # GCP does not fully stop an instance even after
    # the stop operation is finished.
    for _ in range(MAX_POLLS_STOP):
        instances = instance_handler.filter(project_id,
                                            zone,
                                            name_filter,
                                            _NON_STOPPED_STATES,
                                            included_instances=instances)
        if not instances:
            break
        time.sleep(POLL_INTERVAL)
    else:
        raise RuntimeError(f'Maximum number of polls: '
                           f'{MAX_POLLS_STOP} reached. '
                           f'Instance {instances} is still not in '
                           'STOPPED status.')


def terminate_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name
    assert provider_config is not None, cluster_name
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    instance_handler = instance_utils.GCPComputeInstance
    name_filter = {TAG_RAY_CLUSTER_NAME: cluster_name}
    instances = instance_handler.filter(project_id, zone, name_filter,
                                        _NEED_TO_TERMINATE_STATES,
                                        included_instances, excluded_instances)
    operations = []
    for instance in instances:
        operations.append(instance_handler.terminate(project_id, zone,
                                                     instance))
    _wait_for_operations(instance_handler, operations, project_id, zone)
    # We don't wait for the instances to be terminated, as it can take a long
    # time (same as what we did in ray's node_provider).
