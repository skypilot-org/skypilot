"""OCI instance provisioning.

History:
 - Hysun He (hysun.he@oracle.com) @ Oct.16, 2024: Initial implementation
 - Hysun He (hysun.he@oracle.com) @ Nov.13, 2024: Implement open_ports
   and cleanup_ports for supporting SkyServe.
"""

import copy
from datetime import datetime
import time
from typing import Any, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import oci as oci_adaptor
from sky.clouds.utils import oci_utils
from sky.provision import common
from sky.provision import constants
from sky.provision.oci import query_utils
from sky.provision.oci.query_utils import query_helper
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


@query_utils.debug_enabled(logger)
@common_utils.retry
def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """Query instances.

    Returns a dictionary of instance IDs and status.

    A None status means the instance is marked as "terminated"
    or "terminating".
    """
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']

    status_map = oci_utils.oci_config.STATE_MAPPING_OCI_TO_SKY
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}

    instances = _get_filtered_nodes(region, filters)
    for node in instances:
        vm_status = node['status']
        sky_status = status_map[vm_status]
        if non_terminated_only and sky_status is None:
            continue
        statuses[node['inst_id']] = sky_status

    return statuses


@query_utils.debug_enabled(logger)
def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Start instances with bootstrapped configuration."""
    tags = dict(sorted(copy.deepcopy(config.tags).items()))

    start_time = round(time.time() * 1000)
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}

    # Starting stopped nodes if resume_stopped_nodes=True
    resume_instances = []
    if config.resume_stopped_nodes:
        logger.debug('Checking existing stopped nodes.')

        existing_instances = _get_filtered_nodes(region, filters)
        if len(existing_instances) > config.count:
            raise RuntimeError(
                'The number of pending/running/stopped/stopping '
                f'instances combined ({len(existing_instances)}) in '
                f'cluster "{cluster_name_on_cloud}" is greater than the '
                f'number requested by the user ({config.count}). '
                'This is likely a resource leak. '
                'Use "sky down" to terminate the cluster.')

        # pylint: disable=line-too-long
        logger.debug(
            f'run_instances: Found {[inst["name"] for inst in existing_instances]} '
            'existing instances in cluster.')
        existing_instances.sort(key=lambda x: x['name'])

        stopped_instances = []
        for existing_node in existing_instances:
            if existing_node['status'] == 'STOPPING':
                query_helper.wait_instance_until_status(
                    region, existing_node['inst_id'], 'STOPPED')
                stopped_instances.append(existing_node)
            elif existing_node['status'] == 'STOPPED':
                stopped_instances.append(existing_node)
            elif existing_node['status'] in ('PROVISIONING', 'STARTING',
                                             'RUNNING'):
                resume_instances.append(existing_node)

        for stopped_node in stopped_instances:
            stopped_node_id = stopped_node['inst_id']
            instance_action_response = query_helper.start_instance(
                region, stopped_node_id)

            starting_inst = instance_action_response.data
            resume_instances.append({
                'inst_id': starting_inst.id,
                'name': starting_inst.display_name,
                'ad': starting_inst.availability_domain,
                'compartment': starting_inst.compartment_id,
                'status': starting_inst.lifecycle_state,
                'oci_tags': starting_inst.freeform_tags,
            })
    # end if config.resume_stopped_nodes

    # Try get head id from the existing instances
    head_instance_id = _get_head_instance_id(resume_instances)
    logger.debug(f'Check existing head node: {head_instance_id}')

    # Let's create additional new nodes (if neccessary)
    to_start_count = config.count - len(resume_instances)
    created_instances = []
    if to_start_count > 0:
        node_config = config.node_config
        compartment = query_helper.find_compartment(region)
        vcn = query_helper.find_create_vcn_subnet(region)

        ocpu_count = 0
        vcpu_str = node_config['VCPUs']
        instance_type_str = node_config['InstanceType']

        if vcpu_str is not None and vcpu_str != 'None':
            if instance_type_str.startswith(
                    f'{oci_utils.oci_config.VM_PREFIX}.A'):
                # For ARM cpu, 1*ocpu = 1*vcpu
                ocpu_count = round(float(vcpu_str))
            else:
                # For Intel / AMD cpu, 1*ocpu = 2*vcpu
                ocpu_count = round(float(vcpu_str) / 2)
        ocpu_count = 1 if (ocpu_count > 0 and ocpu_count < 1) else ocpu_count

        machine_shape_config = None
        if ocpu_count > 0:
            mem = node_config['MemoryInGbs']
            if mem is not None and mem != 'None':
                # pylint: disable=line-too-long
                machine_shape_config = oci_adaptor.oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=ocpu_count, memory_in_gbs=mem)
            else:
                # pylint: disable=line-too-long
                machine_shape_config = oci_adaptor.oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=ocpu_count)

        preempitible_config = (
            oci_adaptor.oci.core.models.PreemptibleInstanceConfigDetails(
                preemption_action=oci_adaptor.oci.core.models.
                TerminatePreemptionAction(type='TERMINATE',
                                          preserve_boot_volume=False))
            if node_config['Preemptible'] else None)

        batch_id = datetime.now().strftime('%Y%m%d%H%M%S')

        vm_tags_head = {
            **tags,
            **constants.HEAD_NODE_TAGS,
            constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
            'sky_spot_flag': str(node_config['Preemptible']).lower(),
        }
        vm_tags_worker = {
            **tags,
            **constants.WORKER_NODE_TAGS,
            constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
            'sky_spot_flag': str(node_config['Preemptible']).lower(),
        }

        for seq in range(1, to_start_count + 1):
            if head_instance_id is None:
                vm_tags = vm_tags_head
                node_type = constants.HEAD_NODE_TAGS[
                    constants.TAG_RAY_NODE_KIND]
            else:
                vm_tags = vm_tags_worker
                node_type = constants.WORKER_NODE_TAGS[
                    constants.TAG_RAY_NODE_KIND]

            launch_instance_response = query_helper.launch_instance(
                region,
                oci_adaptor.oci.core.models.LaunchInstanceDetails(
                    availability_domain=node_config['AvailabilityDomain'],
                    compartment_id=compartment,
                    shape=instance_type_str,
                    display_name=
                    f'{cluster_name_on_cloud}_{node_type}_{batch_id}_{seq}',
                    freeform_tags=vm_tags,
                    metadata={
                        'ssh_authorized_keys': node_config['AuthorizedKey']
                    },
                    source_details=oci_adaptor.oci.core.models.
                    InstanceSourceViaImageDetails(
                        source_type='image',
                        image_id=node_config['ImageId'],
                        boot_volume_size_in_gbs=node_config['BootVolumeSize'],
                        boot_volume_vpus_per_gb=int(
                            node_config['BootVolumePerf']),
                    ),
                    create_vnic_details=oci_adaptor.oci.core.models.
                    CreateVnicDetails(
                        assign_public_ip=True,
                        subnet_id=vcn,
                    ),
                    shape_config=machine_shape_config,
                    preemptible_instance_config=preempitible_config,
                ))

            new_inst = launch_instance_response.data
            if head_instance_id is None:
                head_instance_id = new_inst.id
                logger.debug(f'New head node: {head_instance_id}')

            created_instances.append({
                'inst_id': new_inst.id,
                'name': new_inst.display_name,
                'ad': new_inst.availability_domain,
                'compartment': new_inst.compartment_id,
                'status': new_inst.lifecycle_state,
                'oci_tags': new_inst.freeform_tags,
            })
        # end for loop
    # end if to_start_count > 0:...

    for inst in (resume_instances + created_instances):
        logger.debug(f'Provisioning for node {inst["name"]}')
        query_helper.wait_instance_until_status(region, inst['inst_id'],
                                                'RUNNING')
        logger.debug(f'Instance {inst["name"]} is RUNNING.')

    total_time = round(time.time() * 1000) - start_time
    logger.debug('Total time elapsed: {0} milli-seconds.'.format(total_time))

    assert head_instance_id is not None, head_instance_id

    return common.ProvisionRecord(
        provider_name='oci',
        region=region,
        zone=None,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_instance_id,
        created_instance_ids=[n['inst_id'] for n in created_instances],
        resumed_instance_ids=[n['inst_id'] for n in resume_instances],
    )


@query_utils.debug_enabled(logger)
def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """Stop running instances."""
    # pylint: disable=line-too-long
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)

    region = provider_config['region']
    tag_filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        tag_filters[constants.TAG_RAY_NODE_KIND] = 'worker'

    nodes = _get_filtered_nodes(region, tag_filters)
    for node in nodes:
        query_helper.stop_instance(region, node['inst_id'])


@query_utils.debug_enabled(logger)
def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """Terminate running or stopped instances."""
    region = provider_config['region']
    tag_filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        tag_filters[constants.TAG_RAY_NODE_KIND] = 'worker'
    query_helper.terminate_instances_by_tags(tag_filters, region)


@query_utils.debug_enabled(logger)
def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Open ports for inbound traffic."""
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    query_helper.create_nsg_rules(region=region,
                                  cluster_name=cluster_name_on_cloud,
                                  ports=ports)


@query_utils.debug_enabled(logger)
def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Delete any opened ports."""
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    del ports
    query_helper.remove_cluster_nsg(region=region,
                                    cluster_name=cluster_name_on_cloud)


@query_utils.debug_enabled(logger)
def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state
    # We already wait for the instances to be running in run_instances.
    # We can not implement the wait logic here because the provisioning
    # instances are not retrieveable by the QL 'query instance resources ...'.


@query_utils.debug_enabled(logger)
def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """Get the metadata of instances in a cluster."""
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    running_instances = _get_filtered_nodes(region, filters)

    instances = {}
    for running_instance in running_instances:
        inst = _get_inst_obj_with_ip(region, running_instance)
        instances[inst['id']] = [
            common.InstanceInfo(
                instance_id=inst['id'],
                internal_ip=inst['internal_ip'],
                external_ip=inst['external_ip'],
                tags=inst['tags'],
            )
        ]

    instances = dict(sorted(instances.items(), key=lambda x: x[0]))
    logger.debug(f'Cluster info: {instances}')

    head_instance_id = _get_head_instance_id(running_instances)
    logger.debug(f'Head instance id is {head_instance_id}')

    return common.ClusterInfo(
        provider_name='oci',
        head_instance_id=head_instance_id,
        instances=instances,
        provider_config=provider_config,
    )


def _get_filtered_nodes(region: str,
                        tag_filters: Dict[str, str]) -> List[Dict[str, Any]]:
    return_nodes = []

    try:
        insts = query_helper.query_instances_by_tags(tag_filters, region)
    except oci_adaptor.oci.exceptions.ServiceError as e:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterStatusFetchingError(
                f'Failed to query status for OCI cluster {tag_filters}.'
                'Details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    for inst in insts:
        inst_id = inst.identifier
        return_nodes.append({
            'inst_id': inst_id,
            'name': inst.display_name,
            'ad': inst.availability_domain,
            'compartment': inst.compartment_id,
            'status': inst.lifecycle_state,
            'oci_tags': inst.freeform_tags,
        })

    return return_nodes


def _get_inst_obj_with_ip(region: str, inst_info: Dict[str,
                                                       Any]) -> Dict[str, Any]:
    get_vnic_response = query_helper.get_instance_primary_vnic(
        region, inst_info)
    internal_ip = get_vnic_response.private_ip
    external_ip = get_vnic_response.public_ip
    if external_ip is None:
        external_ip = internal_ip

    return {
        'id': inst_info['inst_id'],
        'name': inst_info['name'],
        'external_ip': external_ip,
        'internal_ip': internal_ip,
        'tags': inst_info['oci_tags'],
        'status': inst_info['status'],
    }


def _get_head_instance_id(instances: List[Dict[str, Any]]) -> Optional[str]:
    head_instance_id = None
    head_node_tags = tuple(constants.HEAD_NODE_TAGS.items())
    for inst in instances:
        is_matched = True
        for k, v in head_node_tags:
            if (k, v) not in inst['oci_tags'].items():
                is_matched = False
                break
        if is_matched:
            if head_instance_id is not None:
                logger.warning(
                    'There are multiple head nodes in the cluster '
                    f'(current head instance id: {head_instance_id}, '
                    f'newly discovered id: {inst["inst_id"]}. It is likely '
                    f'that something goes wrong.')
                # Don't break here so that we can continue to check and
                # warn user about duplicate head instance issue so that
                # user can take further action on the abnormal cluster.

            head_instance_id = inst['inst_id']

    return head_instance_id
