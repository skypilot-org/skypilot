"""Slurm instance provisioning."""

import collections
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Type

from sky import sky_logging
from sky.provision import common
from sky.provision import constants as provision_constants
from sky.provision.gcp import instance_utils
from sky.provision.slurm import utils as slurm_utils
from sky.utils import command_runner
from sky.utils import common_utils
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
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
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
    statuses = {}
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
        statuses[inst_id] = status

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
    provider_config = config.provider_config
    cluster_name = slurm_utils.get_cluster_name_from_config(provider_config)
    partition = slurm_utils.get_partition_from_config(provider_config)

    # Resume any stopped worker nodes or create new ones.
    # Use sbatch to submit a long-running job.
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    ssh_key = ssh_config_dict['private_key']
    runner = command_runner.SlurmCommandRunner((ssh_host, ssh_port),
                                               ssh_user,
                                               ssh_key,
                                               cluster_name,
                                               partition,
                                               disable_control_master=True)

    provision_script = """#!/bin/bash
#SBATCH --job-name=interactive-bash
#SBATCH --output=interactive-bash.out
#SBATCH --error=interactive-bash.err
#SBATCH --ntasks=1
#SBATCH --time=1:00:00

sleep 1000000000
"""
    # with tempfile.NamedTemporaryFile(mode='w+', delete=True) as f:
    with open('./provision.sh', 'w') as f:
        f.write(provision_script)
        src_path = f.name
        logger.critical(src_path)
    tgt_path = '/tmp/provision.sh'
    runner.rsync(src_path, tgt_path, up=True)
    rc, stdout, stderr = runner.run(f'sbatch {tgt_path}', require_outputs=True)
    # logger.critical(f"\n\n[RUN INSTANCE] {returncode} {stdout} {stderr}\n\n")
    if rc == 0:
        logger.info(stdout)
    provision_job_id = stdout.split(' ')[-1]

    # Wait until the instance is running.

    # Handle instance identifiers.
    head_instance_id = f'{ssh_host}-{provision_job_id}'
    created_instance_ids = [head_instance_id]

    assert head_instance_id is not None, 'head_instance_id is None'
    return common.ProvisionRecord(provider_name='slurm',
                                  region=region,
                                  zone=None,
                                  cluster_name=cluster_name_on_cloud,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    try:
        return _run_instances(region, cluster_name_on_cloud, config)
    except common.ProvisionerError('Failed to launch instances.') as e:
        raise


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
    del region
    assert provider_config is not None, cluster_name_on_cloud
    cluster_name = slurm_utils.get_cluster_name_from_config(provider_config)
    partition = slurm_utils.get_partition_from_config(provider_config)

    # The SSH host is the remote machine running slurmctld daemon.
    # Cross-cluster operations are supported by interacting with
    # the current controller. For details, please refer to
    # https://slurm.schedmd.com/multi_cluster.html.
    ssh_config_dict = provider_config['ssh']
    ssh_host = ssh_config_dict['hostname']
    ssh_port = ssh_config_dict['port']
    ssh_user = ssh_config_dict['user']
    running_jobs = slurm_utils.filter_jobs(ssh_config_dict, partition,
                                           ['running'], cluster_name)

    jobs: Dict[str, List[common.InstanceInfo]] = {}
    head_job_id = None
    for i, job_id in enumerate(running_jobs):
        jobs[job_id] = [
            common.InstanceInfo(instance_id=job_id,
                                internal_ip=ssh_host,
                                external_ip=ssh_host,
                                ssh_port=ssh_port,
                                tags={'test': 'test'})
        ]
        if i == 0:
            # NOTE: The head job ID is dummy here. We may not need it.
            head_job_id = job_id

    return common.ClusterInfo(
        instances=jobs,
        head_instance_id=head_job_id,
        ssh_user=ssh_user,
        provider_name='slurm',
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
        instance_utils.GCPManagedInstanceGroup.delete_mig(
            project_id, zone, cluster_name_on_cloud)
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
