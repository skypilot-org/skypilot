"""GCP instance provisioning."""
import collections
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Type

from sky import sky_logging
from sky.provision.gcp import instance_utils

logger = sky_logging.init_logger(__name__)

MAX_POLLS = 12
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_STOP = MAX_POLLS * 8
POLL_INTERVAL = 5

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'


def _filter_instances(
    handlers: Iterable[Type[instance_utils.GCPInstance]],
    project_id: str,
    zone: str,
    label_filters: Dict[str, str],
    status_filters_fn: Callable[[Type[instance_utils.GCPInstance]],
                                Optional[List[str]]],
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> Dict[Type[instance_utils.GCPInstance], List[str]]:
    """Filter instances using all instance handlers."""
    instances = set()
    logger.debug(f'handlers: {handlers}')
    for instance_handler in handlers:
        instances |= set(
            instance_handler.filter(project_id, zone, label_filters,
                                    status_filters_fn(instance_handler),
                                    included_instances, excluded_instances))
    handler_to_instances = collections.defaultdict(list)
    for instance in instances:
        handler = instance_utils.instance_to_handler(instance)
        handler_to_instances[handler].append(instance)
    logger.debug(f'handler_to_instances: {handler_to_instances}')
    return handler_to_instances


def _wait_for_operations(
    handlers_to_operations: Dict[Type[instance_utils.GCPInstance], List[dict]],
    project_id: str,
    zone: str,
) -> None:
    """Poll for compute zone operation until finished."""
    total_polls = 0
    for handler, operations in handlers_to_operations.items():
        for operation in operations:
            logger.debug(
                'wait_for_compute_zone_operation: '
                f'Waiting for operation {operation["name"]} to finish...')
            while total_polls < MAX_POLLS:
                if handler.wait_for_operation(operation, project_id, zone):
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
    name_filter = {TAG_RAY_CLUSTER_NAME: cluster_name}

    handlers: List[Type[instance_utils.GCPInstance]] = [
        instance_utils.GCPComputeInstance
    ]
    use_tpu_vms = provider_config.get('_has_tpus', False)
    if use_tpu_vms:
        handlers.append(instance_utils.GCPTPUVMInstance)

    handler_to_instances = _filter_instances(
        handlers,
        project_id,
        zone,
        name_filter,
        lambda handler: handler.NEED_TO_STOP_STATES,
        included_instances,
        excluded_instances,
    )
    all_instances = [
        i for instances in handler_to_instances.values() for i in instances
    ]

    operations = collections.defaultdict(list)
    for handler, instances in handler_to_instances.items():
        for instance in instances:
            operations[handler].append(handler.stop(project_id, zone, instance))
    _wait_for_operations(operations, project_id, zone)
    # Check if the instance is actually stopped.
    # GCP does not fully stop an instance even after
    # the stop operation is finished.
    for _ in range(MAX_POLLS_STOP):
        handler_to_instances = _filter_instances(
            handler_to_instances.keys(),
            project_id,
            zone,
            name_filter,
            lambda handler: handler.NON_STOPPED_STATES,
            included_instances=all_instances,
        )
        if not handler_to_instances:
            break
        time.sleep(POLL_INTERVAL)
    else:
        raise RuntimeError(f'Maximum number of polls: '
                           f'{MAX_POLLS_STOP} reached. '
                           f'Instance {all_instances} is still not in '
                           'STOPPED status.')


def terminate_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    use_tpu_vms = provider_config.get('_has_tpus', False)

    name_filter = {TAG_RAY_CLUSTER_NAME: cluster_name}
    handlers: List[Type[instance_utils.GCPInstance]] = [
        instance_utils.GCPComputeInstance
    ]
    if use_tpu_vms:
        handlers.append(instance_utils.GCPTPUVMInstance)

    handler_to_instances = _filter_instances(handlers, project_id, zone,
                                             name_filter, lambda _: None,
                                             included_instances,
                                             excluded_instances)
    operations = collections.defaultdict(list)
    for handler, instances in handler_to_instances.items():
        for instance in instances:
            operations[handler].append(
                handler.terminate(project_id, zone, instance))
    _wait_for_operations(operations, project_id, zone)
    # We don't wait for the instances to be terminated, as it can take a long
    # time (same as what we did in ray's node_provider).
