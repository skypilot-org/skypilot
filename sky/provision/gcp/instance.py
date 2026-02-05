"""GCP instance provisioning."""
import collections
import copy
from multiprocessing import pool
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type

from sky import sky_logging
from sky.adaptors import gcp
from sky.provision import common
from sky.provision import constants as provision_constants
from sky.provision.gcp import config as gcp_config
from sky.provision.gcp import constants
from sky.provision.gcp import instance_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

_INSTANCE_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/zones/.*/instances/.*\' was not found')


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
        instance_dict = instance_handler.filter(
            project_id, zone, label_filters,
            status_filters_fn(instance_handler), included_instances,
            excluded_instances)
        instances |= set(instance_dict.keys())
    handler_to_instances = collections.defaultdict(list)
    for instance in instances:
        handler = instance_utils.instance_to_handler(instance)
        handler_to_instances[handler].append(instance)
    logger.debug(f'handler_to_instances: {handler_to_instances}')
    return handler_to_instances


# TODO(suquark): Does it make sense to not expose this and always assume
# non_terminated_only=True?
# Will there be callers who would want this to be False?
# stop() and terminate() for example already implicitly assume non-terminated.
# Currently, even with non_terminated_only=False, we may not have a dict entry
# for terminated instances, if they have already been fully deleted.
@common_utils.retry
def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # unused
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    label_filters = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }

    handler: Type[
        instance_utils.GCPInstance] = instance_utils.GCPComputeInstance
    use_tpu_vms = provider_config.get('_has_tpus', False)
    if use_tpu_vms:
        handler = instance_utils.GCPTPUVMInstance

    instances = handler.filter(
        project_id,
        zone,
        label_filters,
        status_filters=None,
    )

    raw_statuses = {}
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for inst_id, instance in instances.items():
        raw_status = instance[handler.STATUS_FIELD]
        raw_statuses[inst_id] = raw_status
        if raw_status in handler.PENDING_STATES:
            status = status_lib.ClusterStatus.INIT
        elif raw_status in handler.STOPPING_STATES + handler.STOPPED_STATES:
            status = status_lib.ClusterStatus.STOPPED
        elif raw_status == handler.RUNNING_STATE:
            status = status_lib.ClusterStatus.UP
        else:
            status = None
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = (status, None)

    # GCP does not clean up preempted TPU VMs. We remove it ourselves.
    if handler == instance_utils.GCPTPUVMInstance:
        all_preempted = all(s == 'PREEMPTED' for s in raw_statuses.values())
        if all_preempted:
            logger.info(
                f'Terminating preempted TPU VM cluster {cluster_name_on_cloud}')
            terminate_instances(cluster_name_on_cloud, provider_config)
    # TODO(zhwu): TPU node should check the status of the attached TPU as well.
    return statuses


def _wait_for_operations(
    handlers_to_operations: Dict[Type[instance_utils.GCPInstance], List[dict]],
    project_id: str,
    zone: Optional[str],
) -> None:
    """Poll for compute zone / global operation until finished.

    If zone is None, then the operation is global.
    """
    op_type = 'global' if zone is None else 'zone'
    for handler, operations in handlers_to_operations.items():
        for operation in operations:
            logger.debug(
                f'wait_for_compute_{op_type}_operation: '
                f'Waiting for operation {operation["name"]} to finish...')
            handler.wait_for_operation(operation, project_id, zone=zone)


def _get_head_instance_id(instances: List) -> Optional[str]:
    head_instance_id = None
    for inst in instances:
        labels = inst.get('labels', {})
        if (labels.get(provision_constants.TAG_RAY_NODE_KIND) == 'head' or
                labels.get(provision_constants.TAG_SKYPILOT_HEAD_NODE) == '1'):
            head_instance_id = inst['name']
            break
    return head_instance_id


def _run_instances(region: str, cluster_name_on_cloud: str,
                   config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    # NOTE: although google cloud instances have IDs, but they are
    #  not used for indexing. Instead, we use the instance name.
    labels = config.tags  # gcp uses 'labels' instead of aws 'tags'
    labels = dict(sorted(copy.deepcopy(labels).items()))
    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []

    node_type = instance_utils.get_node_type(config.node_config)
    project_id = config.provider_config['project_id']
    availability_zone = config.provider_config['availability_zone']

    # SKY: 'TERMINATED' for compute VM, 'STOPPED' for TPU VM
    # 'STOPPING' means the VM is being stopped, which needs
    # to be included to avoid creating a new VM.
    resource: Type[instance_utils.GCPInstance]
    if node_type == instance_utils.GCPNodeType.COMPUTE:
        resource = instance_utils.GCPComputeInstance
    elif node_type == instance_utils.GCPNodeType.MIG:
        resource = instance_utils.GCPManagedInstanceGroup
    elif node_type == instance_utils.GCPNodeType.TPU:
        resource = instance_utils.GCPTPUVMInstance
    else:
        raise ValueError(f'Unknown node type {node_type}')

    filter_labels = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }

    # wait until all stopping instances are stopped/terminated
    while True:
        instances = resource.filter(
            project_id=project_id,
            zone=availability_zone,
            label_filters=filter_labels,
            status_filters=resource.STOPPING_STATES,
        )
        if not instances:
            break
        logger.info(f'run_instances: Waiting for {len(instances)} instances in '
                    'STOPPING status')
        time.sleep(constants.POLL_INTERVAL)

    exist_instances = resource.filter(
        project_id=project_id,
        zone=availability_zone,
        label_filters=filter_labels,
        status_filters=None,
    )
    exist_instances = list(exist_instances.values())
    head_instance_id = _get_head_instance_id(exist_instances)

    # NOTE: We are not handling REPAIRING, SUSPENDING, SUSPENDED status.
    pending_instances = []
    running_instances = []
    stopping_instances = []
    stopped_instances = []

    # SkyPilot: We try to use the instances with the same matching launch_config
    # first. If there is not enough instances with matching launch_config, we
    # then use all the instances with the same matching launch_config plus some
    # instances with wrong launch_config.
    def get_order_key(node):
        import datetime  # pylint: disable=import-outside-toplevel

        timestamp = node.get('lastStartTimestamp')
        if timestamp is not None:
            return datetime.datetime.strptime(timestamp,
                                              '%Y-%m-%dT%H:%M:%S.%f%z')
        return node['id']

    logger.info(str(exist_instances))
    for inst in exist_instances:
        state = inst[resource.STATUS_FIELD]
        if state in resource.PENDING_STATES:
            pending_instances.append(inst)
        elif state == resource.RUNNING_STATE:
            running_instances.append(inst)
        elif state in resource.STOPPING_STATES:
            stopping_instances.append(inst)
        elif state in resource.STOPPED_STATES:
            stopped_instances.append(inst)
        else:
            raise RuntimeError(f'Unsupported state "{state}".')

    pending_instances.sort(key=get_order_key, reverse=True)
    running_instances.sort(key=get_order_key, reverse=True)
    stopping_instances.sort(key=get_order_key, reverse=True)
    stopped_instances.sort(key=get_order_key, reverse=True)

    if stopping_instances:
        raise RuntimeError(
            'Some instances are being stopped during provisioning. '
            'Please wait a while and retry.')

    if head_instance_id is None:
        if running_instances:
            head_instance_id = resource.create_node_tag(
                project_id,
                availability_zone,
                running_instances[0]['name'],
                is_head=True,
            )
        elif pending_instances:
            head_instance_id = resource.create_node_tag(
                project_id,
                availability_zone,
                pending_instances[0]['name'],
                is_head=True,
            )
    # TODO(suquark): Maybe in the future, users could adjust the number
    #  of instances dynamically. Then this case would not be an error.
    if config.resume_stopped_nodes and len(exist_instances) > config.count:
        raise RuntimeError(
            'The number of running/stopped/stopping '
            f'instances combined ({len(exist_instances)}) in '
            f'cluster "{cluster_name_on_cloud}" is greater than the '
            f'number requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    to_start_count = (config.count - len(running_instances) -
                      len(pending_instances))

    # Try to reuse previously stopped nodes with compatible configs
    if config.resume_stopped_nodes and to_start_count > 0 and stopped_instances:
        resumed_instance_ids = [n['name'] for n in stopped_instances]
        if resumed_instance_ids:
            resumed_instance_ids = resource.start_instances(
                cluster_name_on_cloud, project_id, availability_zone,
                resumed_instance_ids, labels)
        # In MIG case, the resumed_instance_ids will include the previously
        # PENDING and RUNNING instances. To avoid double counting, we need to
        # remove them from the resumed_instance_ids.
        ready_instances = set(resumed_instance_ids)
        ready_instances |= set([n['name'] for n in running_instances])
        ready_instances |= set([n['name'] for n in pending_instances])
        to_start_count = config.count - len(ready_instances)

        if head_instance_id is None:
            head_instance_id = resource.create_node_tag(
                project_id,
                availability_zone,
                resumed_instance_ids[0],
                is_head=True,
            )

    if to_start_count > 0:
        errors, created_instance_ids = resource.create_instances(
            cluster_name_on_cloud,
            project_id,
            availability_zone,
            config.node_config,
            labels,
            to_start_count,
            total_count=config.count,
            include_head_node=head_instance_id is None)
        if errors:
            error = common.ProvisionerError('Failed to launch instances.')
            error.errors = errors
            raise error
        if head_instance_id is None:
            head_instance_id = created_instance_ids[0]

    while True:
        # wait until all instances are running
        instances = resource.filter(
            project_id=project_id,
            zone=availability_zone,
            label_filters=filter_labels,
            status_filters=resource.PENDING_STATES,
        )
        if not instances:
            break
        logger.debug(f'run_instances: Waiting for {len(instances)} instances '
                     'in PENDING status.')
        time.sleep(constants.POLL_INTERVAL)

    # Check if the number of running instances is the same as the requested.
    instances = resource.filter(
        project_id=project_id,
        zone=availability_zone,
        label_filters=filter_labels,
        status_filters=[resource.RUNNING_STATE],
    )
    if len(instances) != config.count:
        logger.warning('The number of running instances is different from '
                       'the requested number after provisioning '
                       f'(requested: {config.count}, '
                       f'observed: {len(instances)}). '
                       'This could be some instances failed to start '
                       'or some resource leak.')

    assert head_instance_id is not None, 'head_instance_id is None'

    tpu_node = config.provider_config.get('tpu_node')
    if tpu_node is not None:
        vpc_name = resource.get_vpc_name(project_id, availability_zone,
                                         head_instance_id)
        assert config.count == 1, 'TPU node only supports 1 instance'
        instance_utils.create_tpu_node(
            project_id,
            availability_zone,
            tpu_node,
            vpc_name,
        )
    return common.ProvisionRecord(provider_name='gcp',
                                  region=region,
                                  zone=availability_zone,
                                  cluster_name=cluster_name_on_cloud,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=resumed_instance_ids,
                                  created_instance_ids=created_instance_ids)


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    del cluster_name  # unused
    try:
        return _run_instances(region, cluster_name_on_cloud, config)
    except gcp.http_error_exception() as e:
        error_details = getattr(e, 'error_details')
        errors = []
        if isinstance(error_details, list):
            for detail in error_details:
                errors.append({
                    'code': detail.get('reason'),
                    'domain': detail.get('domain'),
                    'message': detail.get('message', str(e)),
                })
        elif isinstance(error_details, str):
            errors.append({
                'code': None,
                'domain': 'run_instances',
                'message': error_details,
            })
        else:
            raise
        error = common.ProvisionerError('Failed to launch instances.')
        error.errors = errors
        raise error from e


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    del region, cluster_name_on_cloud, state
    # We already wait for the instances to be running in run_instances.
    # So we don't need to wait here.


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    del region
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    label_filters = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }

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
        label_filters,
        lambda h: [h.RUNNING_STATE],
    )
    instances: Dict[str, List[common.InstanceInfo]] = {}
    for res, insts in handler_to_instances.items():
        with pool.ThreadPool() as p:
            inst_info = p.starmap(res.get_instance_info,
                                  [(project_id, zone, inst) for inst in insts])
        instances.update(zip(insts, inst_info))

    head_instances = _filter_instances(
        handlers,
        project_id,
        zone,
        {
            **label_filters, provision_constants.TAG_RAY_NODE_KIND: 'head'
        },
        lambda h: [h.RUNNING_STATE],
    )
    head_instance_id = None
    for insts in head_instances.values():
        if insts and insts[0]:
            head_instance_id = insts[0]
            break

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='gcp',
        provider_config=provider_config,
    )


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    label_filters = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }

    tpu_node = provider_config.get('tpu_node')
    if tpu_node is not None:
        instance_utils.delete_tpu_node(project_id, zone, tpu_node)

    if worker_only:
        label_filters[provision_constants.TAG_RAY_NODE_KIND] = 'worker'

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
        label_filters,
        lambda handler: handler.NEED_TO_STOP_STATES,
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
    for _ in range(constants.MAX_POLLS_STOP):
        handler_to_instances = _filter_instances(
            handler_to_instances.keys(),
            project_id,
            zone,
            label_filters,
            lambda handler: handler.NON_STOPPED_STATES,
            included_instances=all_instances,
        )
        if not handler_to_instances:
            break
        time.sleep(constants.POLL_INTERVAL)
    else:
        raise RuntimeError(f'Maximum number of polls: '
                           f'{constants.MAX_POLLS_STOP} reached. '
                           f'Instance {all_instances} is still not in '
                           'STOPPED status.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    use_tpu_vms = provider_config.get('_has_tpus', False)

    tpu_node = provider_config.get('tpu_node')
    if tpu_node is not None:
        instance_utils.delete_tpu_node(project_id, zone, tpu_node)

    use_mig = provider_config.get('use_managed_instance_group', False)
    if use_mig:
        # Deleting the MIG will also delete the instances.
        mig_exists_and_deleted = (
            instance_utils.GCPManagedInstanceGroup.delete_mig(
                project_id, zone, cluster_name_on_cloud))
        if mig_exists_and_deleted:
            return

    label_filters = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }
    if worker_only:
        label_filters[provision_constants.TAG_RAY_NODE_KIND] = 'worker'

    handlers: List[Type[instance_utils.GCPInstance]] = [
        instance_utils.GCPComputeInstance
    ]
    if use_tpu_vms:
        handlers.append(instance_utils.GCPTPUVMInstance)

    handler_to_instances = _filter_instances(handlers, project_id, zone,
                                             label_filters, lambda _: None)
    operations = collections.defaultdict(list)
    errs = []
    for handler, instances in handler_to_instances.items():
        for instance in instances:
            try:
                logger.debug(f'Terminating instance: {instance}.')
                operations[handler].append(
                    handler.terminate(project_id, zone, instance))
            except gcp.http_error_exception() as e:
                if _INSTANCE_RESOURCE_NOT_FOUND_PATTERN.search(
                        e.reason) is None:
                    errs.append(e)
                else:
                    logger.warning(f'Instance {instance} does not exist. '
                                   'Skip terminating it.')
    _wait_for_operations(operations, project_id, zone)
    if errs:
        raise RuntimeError(f'Failed to terminate instances: {errs}')
    # We don't wait for the instances to be terminated, as it can take a long
    # time (same as what we did in ray's node_provider).


def cleanup_custom_multi_network(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    failover: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    project_id = provider_config['project_id']
    region = provider_config['region']
    enable_gpu_direct = provider_config.get('enable_gpu_direct', False)
    network_tier = provider_config.get('network_tier', 'standard')

    if (enable_gpu_direct or
            network_tier == resources_utils.NetworkTier.BEST.value):
        gcp_config.delete_gpu_direct_vpcs_and_subnets(cluster_name_on_cloud,
                                                      project_id, region,
                                                      failover)


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    firewall_rule_name = provider_config['firewall_rule']

    label_filters = {
        provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud
    }
    handlers: List[Type[instance_utils.GCPInstance]] = [
        instance_utils.GCPComputeInstance,
    ]
    use_tpu_vms = provider_config.get('_has_tpus', False)
    if use_tpu_vms:
        handlers.append(instance_utils.GCPTPUVMInstance)

    handler_to_instances = _filter_instances(handlers, project_id, zone,
                                             label_filters, lambda _: None)
    operations = collections.defaultdict(list)
    compute_handler: Type[instance_utils.GCPInstance] = (
        instance_utils.GCPComputeInstance)
    for handler, instances in handler_to_instances.items():
        if not instances:
            logger.warning(f'No instance found for cluster '
                           f'{cluster_name_on_cloud}.')
            continue
        else:
            for instance in instances:
                # Add tags for all nodes in the cluster, so the firewall rule
                # could correctly apply to all instance in the cluster.
                handler.add_network_tag_if_not_exist(
                    project_id,
                    zone,
                    instance,
                    tag=cluster_name_on_cloud,
                )
            # If we have multiple instances, they are in the same cluster,
            # i.e. the same VPC. So we can just pick any one of them.
            vpc_name = handler.get_vpc_name(project_id, zone, instances[0])
            # Use compute handler here for both Compute VM and TPU VM,
            # as firewall rules is a compute resource.
            op = compute_handler.create_or_update_firewall_rule(
                firewall_rule_name,
                project_id,
                vpc_name,
                cluster_name_on_cloud,
                ports,
            )
            operations[compute_handler].append(op)
    # Use zone = None to indicate wait for global operations
    _wait_for_operations(operations, project_id, None)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del ports  # Unused.
    assert provider_config is not None, cluster_name_on_cloud
    project_id = provider_config['project_id']
    if 'firewall_rule' in provider_config:
        firewall_rule_name = provider_config['firewall_rule']
        instance_utils.GCPComputeInstance.delete_firewall_rule(
            project_id, firewall_rule_name)
