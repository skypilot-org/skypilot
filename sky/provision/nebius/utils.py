"""Nebius library wrapper for SkyPilot."""
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import nebius
from sky.provision.nebius import constants as nebius_constants
from sky.utils import common_utils
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)

POLL_INTERVAL = 5

_MAX_OPERATIONS_TO_FETCH = 1000

# Re-export for back-compat with existing callers in this module.
SECURITY_GROUP_TEMPLATE = nebius_constants.SECURITY_GROUP_TEMPLATE

# Nebius caps RuleIngress.destination_ports at 8 per rule, so we batch.
_NEBIUS_PORTS_PER_RULE = 8

# Backoff attempts when deleting an SG that's still attached to terminating
# VMs. Mirrors AWS's BOTO_DELETE_MAX_ATTEMPTS (sky/provision/aws/instance.py).
_SG_DELETE_MAX_ATTEMPTS = 6


def retry(func):
    """Decorator to retry a function."""

    def wrapper(*args, **kwargs):
        """Wrapper for retrying a function."""
        cnt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except nebius.nebius.error.QueryError as e:
                if cnt >= 3:
                    raise
                logger.warning('Retrying for exception: '
                               f'{common_utils.format_exception(e)}.')
                time.sleep(POLL_INTERVAL)

    return wrapper


def get_project_by_region(region: str) -> str:
    project_id = skypilot_config.get_effective_region_config(
        cloud='nebius', region=region, keys=('project_id',), default_value=None)
    if project_id is not None:
        return project_id
    service = nebius.iam().ProjectServiceClient(nebius.sdk())
    projects = nebius.sync_call(
        service.list(
            nebius.iam().ListProjectsRequest(parent_id=nebius.get_tenant_id()),
            timeout=nebius.READ_TIMEOUT))
    for project in projects.items:
        if project.status.region == region:
            return project.metadata.id
    raise Exception(f'No project found for region "{region}".')


def get_or_create_gpu_cluster(name: str, project_id: str, fabric: str) -> str:
    """Creates a GPU cluster.
    https://docs.nebius.com/compute/clusters/gpu
    """
    service = nebius.compute().GpuClusterServiceClient(nebius.sdk())
    try:
        cluster = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=name,
            )))
        cluster_id = cluster.metadata.id
    except nebius.request_error():
        cluster = nebius.sync_call(
            service.create(nebius.compute().CreateGpuClusterRequest(
                metadata=nebius.nebius_common().ResourceMetadata(
                    parent_id=project_id,
                    name=name,
                ),
                spec=nebius.compute().GpuClusterSpec(
                    infiniband_fabric=fabric))))
        cluster_id = cluster.resource_id
    return cluster_id


def get_subnet_id(region: str, project_id: str) -> str:
    #  Check is there subnet if in config
    subnet_id = skypilot_config.get_effective_region_config(cloud='nebius',
                                                            region=region,
                                                            keys=('subnet_id',),
                                                            default_value=None)

    service = nebius.vpc().SubnetServiceClient(nebius.sdk())
    sub_nets = nebius.sync_call(
        service.list(nebius.vpc().ListSubnetsRequest(parent_id=project_id,)))

    # If subnet exists in this region return it else return first subnet
    if subnet_id is not None:
        for sub_net in sub_nets.items:
            if sub_net.metadata.id == subnet_id:
                return subnet_id
        logger.warning(
            f'Subnet ID "{subnet_id}" from config not found for region '
            f'{region}. Falling back to the first available subnet.')

    if not sub_nets.items:
        raise ValueError(f'No subnets found for project {project_id} in region '
                         f'{region}.')
    return sub_nets.items[0].metadata.id


def get_network_id_from_subnet(subnet_id: str) -> str:
    """Resolves a Nebius subnet id to its parent network id.

    Security groups bind to a network (`SecurityGroupSpec.network_id`), but
    SkyPilot only tracks subnet ids. This looks up the subnet and returns
    its `spec.network_id`.
    """
    service = nebius.vpc().SubnetServiceClient(nebius.sdk())
    subnet = nebius.sync_call(
        service.get(nebius.vpc().GetSubnetRequest(id=subnet_id),
                    timeout=nebius.READ_TIMEOUT))
    return subnet.spec.network_id


def get_security_group_by_name(project_id: str, sg_name: str) -> Optional[str]:
    """Returns the SG id by name, or None if it doesn't exist."""
    sg = _get_security_group_obj_by_name(project_id, sg_name)
    return None if sg is None else sg.metadata.id


def get_security_group_id_and_network_by_name(
        project_id: str, sg_name: str) -> Optional[Tuple[str, str]]:
    """Returns (sg_id, network_id) by name, or None if it doesn't exist.

    Single-RPC variant used by `bootstrap_instances` to both look up a
    BYO SG and validate it lives in the cluster's network. Avoids a
    TOCTOU between two separate `get_by_name` + `get` calls.
    """
    sg = _get_security_group_obj_by_name(project_id, sg_name)
    if sg is None:
        return None
    return sg.metadata.id, sg.spec.network_id


def _get_security_group_obj_by_name(project_id: str, sg_name: str) -> Any:
    """Returns the full SG object by name, or None if it doesn't exist."""
    service = nebius.vpc().SecurityGroupServiceClient(nebius.sdk())
    try:
        return nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=sg_name,
            )))
    except nebius.request_error():
        return None


def get_or_create_security_group(project_id: str, sg_name: str,
                                 network_id: str) -> str:
    """Returns the SG id, creating the SG (with no rules) if missing.

    Idempotent: safe to call on every `bootstrap_instances`. Mirrors AWS's
    `_get_or_create_vpc_security_group` in `sky.provision.aws.config`.
    """
    service = nebius.vpc().SecurityGroupServiceClient(nebius.sdk())
    existing = get_security_group_by_name(project_id, sg_name)
    if existing is not None:
        return existing
    try:
        op = nebius.sync_call(
            service.create(nebius.vpc().CreateSecurityGroupRequest(
                metadata=nebius.nebius_common().ResourceMetadata(
                    parent_id=project_id,
                    name=sg_name,
                ),
                spec=nebius.vpc().SecurityGroupSpec(network_id=network_id))))
        return op.resource_id
    except nebius.request_error():
        # Race: someone created it between our get_by_name and create.
        # Re-fetch and return.
        sg = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=sg_name,
            )))
        return sg.metadata.id


def list_security_rules(sg_id: str) -> List[Any]:
    """Lists all SecurityRule resources attached to the given SG."""
    service = nebius.vpc().SecurityRuleServiceClient(nebius.sdk())
    rules: List[Any] = []
    page_token = ''
    while True:
        resp = nebius.sync_call(
            service.list(nebius.vpc().ListSecurityRulesRequest(
                parent_id=sg_id,
                page_size=100,
                page_token=page_token,
            ),
                         timeout=nebius.READ_TIMEOUT))
        rules.extend(resp.items)
        if not resp.next_page_token:
            break
        page_token = resp.next_page_token
    return rules


def _create_security_rule(sg_id: str, spec: Any, name_prefix: str) -> None:
    """Creates a single rule under the given SG.

    Nebius requires a non-empty `metadata.name` unique within the parent
    SG; we synthesize it as `{prefix}-{uuid}` so re-runs never collide
    with prior rules even if dedup-by-shape misses an edge case. The name
    is operator-facing only (Nebius console); dedup uses the rule spec.
    """
    rule_name = f'{name_prefix}-{uuid.uuid4().hex[:8]}'
    service = nebius.vpc().SecurityRuleServiceClient(nebius.sdk())
    nebius.sync_call(
        service.create(nebius.vpc().CreateSecurityRuleRequest(
            metadata=nebius.nebius_common().ResourceMetadata(
                parent_id=sg_id,
                name=rule_name,
            ),
            spec=spec)))


def _rule_signature(spec: Any) -> Tuple:
    """Canonicalizes a rule spec for dedup comparison."""
    # The proto's `s_match` oneof reports field-group name ('match'), not the
    # set case — discriminate by which member is populated.
    if spec.ingress is not None:
        ing = spec.ingress
        match = ('ingress', tuple(sorted(ing.source_cidrs or [])),
                 tuple(sorted(ing.destination_ports or
                              [])), ing.source_security_group_id or '')
    else:
        eg = spec.egress
        match = ('egress', tuple(sorted(eg.destination_cidrs or [])),
                 tuple(sorted(eg.destination_ports or
                              [])), eg.destination_security_group_id or '')
    return (int(spec.access), int(spec.protocol)) + match


def ensure_default_sg_rules(sg_id: str) -> None:
    """Idempotently applies the default rule set to the SG.

    Default rules: intra-cluster (self-SG ref, all protocols/ports),
    SSH 22 + 10022 from anywhere, egress allow-all. Mirrors the AWS default
    SG shape — see the inbound-rules block in
    `sky.provision.aws.config._configure_security_group`.
    """
    vpc = nebius.vpc()
    desired = [
        # Intra-cluster: any traffic from peers in the same SG.
        ('intra-cluster',
         vpc.SecurityRuleSpec(
             access=vpc.RuleAccessAction.ALLOW,
             protocol=vpc.RuleProtocol.ANY,
             ingress=vpc.RuleIngress(source_security_group_id=sg_id),
         )),
        ('allow-ssh-22',
         vpc.SecurityRuleSpec(
             access=vpc.RuleAccessAction.ALLOW,
             protocol=vpc.RuleProtocol.TCP,
             ingress=vpc.RuleIngress(source_cidrs=['0.0.0.0/0'],
                                     destination_ports=[22]),
         )),
        ('allow-ssh-10022',
         vpc.SecurityRuleSpec(
             access=vpc.RuleAccessAction.ALLOW,
             protocol=vpc.RuleProtocol.TCP,
             ingress=vpc.RuleIngress(source_cidrs=['0.0.0.0/0'],
                                     destination_ports=[10022]),
         )),
        ('egress-allow-all',
         vpc.SecurityRuleSpec(
             access=vpc.RuleAccessAction.ALLOW,
             protocol=vpc.RuleProtocol.ANY,
             egress=vpc.RuleEgress(destination_cidrs=['0.0.0.0/0']),
         )),
    ]
    existing_sigs = {
        _rule_signature(r.spec) for r in list_security_rules(sg_id)
    }
    for name_prefix, spec in desired:
        if _rule_signature(spec) not in existing_sigs:
            _create_security_rule(sg_id, spec, name_prefix)


def add_ingress_tcp_ports(sg_id: str, ports: Set[int]) -> None:
    """Adds TCP ingress rules from 0.0.0.0/0 for the given ports.

    Idempotent: skips ports already covered by existing rules. Packs up to
    `_NEBIUS_PORTS_PER_RULE` ports per rule (Nebius caps RuleIngress at 8
    destination_ports).
    """
    if not ports:
        return
    vpc = nebius.vpc()
    already_open: Set[int] = set()
    for rule in list_security_rules(sg_id):
        spec = rule.spec
        if int(spec.access) != int(vpc.RuleAccessAction.ALLOW):
            continue
        if int(spec.protocol) != int(vpc.RuleProtocol.TCP):
            continue
        if spec.ingress is None:
            continue
        ing = spec.ingress
        if list(ing.source_cidrs or []) != ['0.0.0.0/0']:
            continue
        already_open.update(ing.destination_ports or [])

    to_add = sorted(p for p in ports if p not in already_open)
    for i in range(0, len(to_add), _NEBIUS_PORTS_PER_RULE):
        batch = to_add[i:i + _NEBIUS_PORTS_PER_RULE]
        spec = vpc.SecurityRuleSpec(
            access=vpc.RuleAccessAction.ALLOW,
            protocol=vpc.RuleProtocol.TCP,
            ingress=vpc.RuleIngress(source_cidrs=['0.0.0.0/0'],
                                    destination_ports=batch),
        )
        _create_security_rule(sg_id, spec,
                              f'tcp-ingress-{batch[0]}-{batch[-1]}')


def _delete_security_rule(rule_id: str) -> None:
    """Deletes a single rule. Swallows NOT_FOUND."""
    service = nebius.vpc().SecurityRuleServiceClient(nebius.sdk())
    try:
        nebius.sync_call(
            service.delete(nebius.vpc().DeleteSecurityRuleRequest(id=rule_id)))
    except nebius.request_error() as e:
        if 'not found' in str(e).lower() or 'not_found' in str(e).lower():
            return
        raise


def delete_security_group(sg_id: str) -> None:
    """Deletes the SG, with rule-drain + retry on dependency errors.

    Nebius will not delete an SG that still contains rules (rules must be
    deleted first) or that is still attached to instances. We:
      1. List + delete all rules in the SG, then poll until the rule list
         is empty (Nebius deletes are async).
      2. Attempt the SG delete with retry-on-dependency-violation (covers
         the case where VMs are still terminating after `terminate_instances`
         returned — mirrors the retry block in
         `sky.provision.aws.instance.cleanup_ports`).

    On exhaustion, log + return rather than raise: an orphaned SG is
    preferable to a teardown failure.
    """
    sg_service = nebius.vpc().SecurityGroupServiceClient(nebius.sdk())

    # Step 1: kick off async deletion of all rules.
    for rule in list_security_rules(sg_id):
        _delete_security_rule(rule.metadata.id)

    # Wait for the rule list to drain. Nebius rule deletion is async; the
    # SG delete in step 2 will fail with FAILED_PRECONDITION until the
    # listing reflects an empty parent.
    for _ in range(30):  # ~60s cap, similar to other waits in this module
        if not list_security_rules(sg_id):
            break
        time.sleep(2)
    else:
        logger.warning(
            f'Security group {sg_id} still has rules after rule-drain '
            f'timeout; leaving as orphan.')
        return

    # Step 2: delete the SG with retry-on-dependency.
    backoff = common_utils.Backoff()
    for attempt in range(_SG_DELETE_MAX_ATTEMPTS):
        try:
            nebius.sync_call(
                sg_service.delete(
                    nebius.vpc().DeleteSecurityGroupRequest(id=sg_id)))
            return
        except nebius.request_error() as e:
            msg = str(e).lower()
            if 'not found' in msg or 'not_found' in msg:
                return  # Already gone; treat as success.
            if attempt + 1 == _SG_DELETE_MAX_ATTEMPTS:
                logger.warning(
                    f'Failed to delete security group {sg_id} after '
                    f'{_SG_DELETE_MAX_ATTEMPTS} attempts: {e}. '
                    f'Leaving as orphan; clean up manually if needed.')
                return
            logger.debug(f'SG {sg_id} delete attempt {attempt + 1} failed: '
                         f'{e}. Retrying.')
            time.sleep(backoff.current_backoff())


def delete_cluster(name: str,
                   region: str,
                   project_id: Optional[str] = None) -> None:
    """Delete a GPU cluster."""
    if project_id is None:
        project_id = get_project_by_region(region)
    service = nebius.compute().GpuClusterServiceClient(nebius.sdk())
    try:
        cluster = nebius.sync_call(
            service.get_by_name(nebius.nebius_common().GetByNameRequest(
                parent_id=project_id,
                name=name,
            )))
        cluster_id = cluster.metadata.id
        logger.debug(f'Found GPU Cluster : {cluster_id}.')
        nebius.sync_call(
            service.delete(
                nebius.compute().DeleteGpuClusterRequest(id=cluster_id)))
        logger.debug(f'Deleted GPU Cluster : {cluster_id}.')
    except nebius.request_error():
        logger.debug('GPU Cluster does not exist.')


def list_instances(project_id: str) -> Dict[str, Dict[str, Any]]:
    """Lists instances associated with API key."""
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    page_token = ''
    instances = []
    while True:
        result = nebius.sync_call(
            service.list(nebius.compute().ListInstancesRequest(
                parent_id=project_id,
                page_size=100,
                page_token=page_token,
            ),
                         timeout=nebius.READ_TIMEOUT))
        instances.extend(result.items)
        if not result.next_page_token:  # "" means no more pages
            break
        page_token = result.next_page_token

    instance_dict: Dict[str, Dict[str, Any]] = {}
    for instance in instances:
        info: Dict[str, Any] = {}
        info['status'] = instance.status.state.name
        info['name'] = instance.metadata.name
        if instance.status.network_interfaces:
            info['external_ip'] = instance.status.network_interfaces[
                0].public_ip_address.address.split('/')[0]
            info['internal_ip'] = instance.status.network_interfaces[
                0].ip_address.address.split('/')[0]
        # Capture which security groups (if any) are attached to NIC 0. Used
        # by bootstrap_instances to detect pre-SG (legacy) clusters that need
        # to be recreated for full network-level firewalling.
        info['security_group_ids'] = []
        if instance.spec.network_interfaces:
            sgs = instance.spec.network_interfaces[0].security_groups or []
            info['security_group_ids'] = [sg.id for sg in sgs]
        instance_dict[instance.metadata.id] = info

    return instance_dict


def stop(instance_id: str) -> None:
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    nebius.sync_call(
        service.stop(nebius.compute().StopInstanceRequest(id=instance_id)))
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_STOP:
        service = nebius.compute().InstanceServiceClient(nebius.sdk())
        instance = nebius.sync_call(
            service.get(nebius.compute().GetInstanceRequest(id=instance_id,)))
        if instance.status.state.name == 'STOPPED':
            break
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {instance_id} stopping.')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_STOP:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_STOP * POLL_INTERVAL}'
            f' seconds) while waiting for instance {instance_id}'
            f' to be stopped.')


def start(instance_id: str) -> None:
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    nebius.sync_call(
        service.start(nebius.compute().StartInstanceRequest(id=instance_id)))
    retry_count = 0
    while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_START:
        service = nebius.compute().InstanceServiceClient(nebius.sdk())
        instance = nebius.sync_call(
            service.get(nebius.compute().GetInstanceRequest(id=instance_id,)))
        if instance.status.state.name == 'RUNNING':
            break
        time.sleep(POLL_INTERVAL)
        logger.debug(f'Waiting for instance {instance_id} starting.')
        retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_START:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_INSTANCE_START * POLL_INTERVAL}'
            f' seconds) while waiting for instance {instance_id}'
            f' to be ready.')


def launch(cluster_name_on_cloud: str,
           node_type: str,
           platform: str,
           preset: str,
           region: str,
           image_id_or_family: str,
           disk_size: int,
           user_data: str,
           associate_public_ip_address: bool,
           filesystems: List[Dict[str, Any]],
           disk_tier: str,
           use_static_ip_address: bool = False,
           use_spot: bool = False,
           network_tier: Optional[resources_utils.NetworkTier] = None,
           security_group_ids: Optional[List[str]] = None) -> str:
    # Each node must have a unique name to avoid conflicts between
    # multiple worker VMs. To ensure uniqueness,a UUID is appended
    # to the node name.
    instance_name = (f'{cluster_name_on_cloud}-'
                     f'{uuid.uuid4().hex[:4]}-{node_type}')
    logger.debug(f'Launching instance: {instance_name}')

    disk_name = 'disk-' + instance_name
    cluster_id = None
    project_id = get_project_by_region(region)
    # 8 GPU virtual machines can be grouped into a GPU cluster.
    # The GPU clusters are built with InfiniBand secure high-speed networking.
    # https://docs.nebius.com/compute/clusters/gpu
    if platform in nebius_constants.INFINIBAND_INSTANCE_PLATFORMS:
        if preset == '8gpu-128vcpu-1600gb':
            fabric = skypilot_config.get_effective_region_config(
                cloud='nebius',
                region=region,
                keys=('fabric',),
                default_value=None)

            # Auto-select fabric if network_tier=best and no fabric configured
            if (fabric is None and
                    str(network_tier) == str(resources_utils.NetworkTier.BEST)):
                try:
                    fabric = nebius_constants.get_default_fabric(
                        platform, region)
                    logger.info(f'Auto-selected InfiniBand fabric {fabric} '
                                f'for {platform} in {region}')
                except ValueError as e:
                    logger.warning(
                        f'InfiniBand fabric auto-selection failed: {e}')

            if fabric is None:
                logger.warning(
                    f'Set up fabric for region {region} in ~/.sky/config.yaml '
                    'to use GPU clusters.')
            else:
                cluster_id = get_or_create_gpu_cluster(cluster_name_on_cloud,
                                                       project_id, fabric)

    def _disk_tier_to_disk_type(disk_tier: str) -> Any:
        tier2type = {
            str(resources_utils.DiskTier.HIGH):
                nebius.compute().DiskSpec.DiskType.NETWORK_SSD_IO_M3,
            str(resources_utils.DiskTier.MEDIUM):
                nebius.compute().DiskSpec.DiskType.NETWORK_SSD,
            str(resources_utils.DiskTier.LOW):
                nebius.compute().DiskSpec.DiskType.NETWORK_SSD_NON_REPLICATED,
        }
        return tier2type[str(disk_tier)]

    # Nebius NETWORK_SSD_IO_M3 (HIGH tier) requires disk sizes to be a
    # multiple of 93 GiB.
    actual_disk_size = disk_size
    if (str(disk_tier) == str(resources_utils.DiskTier.HIGH) or
            str(disk_tier) == str(resources_utils.DiskTier.LOW)):
        actual_disk_size = nebius_constants.round_up_disk_size(disk_size)
        if actual_disk_size != disk_size:
            logger.warning(
                f'Nebius HIGH and LOW disk tier requires size to be a multiple '
                f'of {nebius_constants.NEBIUS_DISK_SIZE_STEP_GIB} GiB. '
                f'Requested {disk_size} GiB, rounding up to '
                f'{actual_disk_size} GiB.')

    disk_spec = nebius.compute().DiskSpec(
        size_gibibytes=actual_disk_size,
        type=_disk_tier_to_disk_type(disk_tier),
    )
    if image_id_or_family.startswith('computeimage-'):
        disk_spec.source_image_id = image_id_or_family
    else:
        disk_spec.source_image_family = nebius.compute().SourceImageFamily(
            image_family=image_id_or_family)

    filesystems_spec = []
    if filesystems:
        for fs in filesystems:
            filesystems_spec.append(nebius.compute().AttachedFilesystemSpec(
                mount_tag=fs['filesystem_mount_tag'],
                attach_mode=nebius.compute().AttachedFilesystemSpec.AttachMode[
                    fs['filesystem_attach_mode']],
                existing_filesystem=nebius.compute().ExistingFilesystem(
                    id=fs['filesystem_id'])))

    sub_net_id = get_subnet_id(region, project_id)
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    logger.debug(f'Creating instance {instance_name} in project {project_id}.')
    try:
        nebius.sync_call(
            service.create(nebius.compute().CreateInstanceRequest(
                metadata=nebius.nebius_common().ResourceMetadata(
                    parent_id=project_id,
                    name=instance_name,
                ),
                spec=nebius.compute().InstanceSpec(
                    gpu_cluster=nebius.compute().InstanceGpuClusterSpec(
                        id=cluster_id,) if cluster_id is not None else None,
                    boot_disk=nebius.compute().AttachedDiskSpec(
                        attach_mode=nebius.compute(
                        ).AttachedDiskSpec.AttachMode.READ_WRITE,
                        managed_disk=nebius.compute().ManagedDisk(
                            spec=disk_spec,
                            name=disk_name,
                        )),
                    cloud_init_user_data=user_data,
                    resources=nebius.compute().ResourcesSpec(platform=platform,
                                                             preset=preset),
                    filesystems=filesystems_spec if filesystems_spec else None,
                    network_interfaces=[
                        nebius.compute().NetworkInterfaceSpec(
                            subnet_id=sub_net_id,
                            ip_address=nebius.compute().IPAddress(),
                            name='network-interface-0',
                            public_ip_address=nebius.compute().PublicIPAddress(
                                static=use_static_ip_address)
                            if associate_public_ip_address else None,
                            security_groups=[
                                nebius.compute().SecurityGroup(id=sg_id)
                                for sg_id in (security_group_ids or [])
                            ] or None,
                        )
                    ],
                    recovery_policy=nebius.compute().InstanceRecoveryPolicy.FAIL
                    if use_spot else None,
                    preemptible=nebius.compute().PreemptibleSpec(
                        priority=1,
                        on_preemption=nebius.compute().PreemptibleSpec.
                        PreemptionPolicy.STOP) if use_spot else None,
                ))))
        instance_id = ''
        retry_count = 0
        while retry_count < nebius.MAX_RETRIES_TO_INSTANCE_READY:
            service = nebius.compute().InstanceServiceClient(nebius.sdk())
            instance = nebius.sync_call(
                service.get_by_name(nebius.nebius_common().GetByNameRequest(
                    parent_id=project_id,
                    name=instance_name,
                )))
            instance_id = instance.metadata.id
            if instance.status.state.name == 'STARTING':
                break

            # All Instances initially have state=STOPPED and reconciling=True,
            # so we need to wait until reconciling is False.
            if instance.status.state.name == 'STOPPED' and \
                    not instance.status.reconciling:
                next_token = ''
                total_operations = 0
                while True:
                    operations_response = nebius.sync_call(
                        service.list_operations_by_parent(
                            nebius.compute().ListOperationsByParentRequest(
                                parent_id=project_id,
                                page_size=100,
                                page_token=next_token,
                            )))
                    total_operations += len(operations_response.operations)
                    for operation in operations_response.operations:
                        # Find the most recent operation for the instance.
                        if operation.resource_id == instance_id:
                            error_msg = operation.description
                            if operation.status:
                                error_msg += f' {operation.status.message}'
                            raise RuntimeError(error_msg)
                    # If we've fetched too many operations, or there are no more
                    # operations to fetch, just raise a generic error.
                    if total_operations > _MAX_OPERATIONS_TO_FETCH or \
                            not operations_response.next_page_token:
                        raise RuntimeError(
                            f'Instance {instance_name} failed to start.')
                    next_token = operations_response.next_page_token
            time.sleep(POLL_INTERVAL)
            logger.debug(
                f'Waiting for instance {instance_name} to start running. '
                f'State: {instance.status.state.name}, '
                f'Reconciling: {instance.status.reconciling}')
            retry_count += 1

        if retry_count == nebius.MAX_RETRIES_TO_INSTANCE_READY:
            raise TimeoutError(
                f'Exceeded maximum retries '
                f'({nebius.MAX_RETRIES_TO_INSTANCE_READY * POLL_INTERVAL}'
                f' seconds) while waiting for instance {instance_name}'
                f' to be ready.')
    except nebius.request_error() as e:
        logger.warning(f'Failed to launch instance {instance_name}: {e}')
        raise e
    return instance_id


def remove(instance_id: str) -> None:
    """Terminates the given instance."""
    service = nebius.compute().InstanceServiceClient(nebius.sdk())
    result = nebius.sync_call(
        service.get(nebius.compute().GetInstanceRequest(id=instance_id)))
    nebius.sync_call(
        service.delete(nebius.compute().DeleteInstanceRequest(id=instance_id)))

    if result.spec.boot_disk.existing_disk is None:
        return
    # Instances create by older versions might still be using
    # attached disks rather than managed disks
    disk_id = result.spec.boot_disk.existing_disk.id

    retry_count = 0
    # The instance begins deleting and attempts to delete the disk.
    # Must wait until the disk is unlocked and becomes deletable.
    while retry_count < nebius.MAX_RETRIES_TO_DISK_DELETE:
        try:
            service = nebius.compute().DiskServiceClient(nebius.sdk())
            nebius.sync_call(
                service.delete(nebius.compute().DeleteDiskRequest(id=disk_id)))
            break
        except nebius.request_error():
            logger.debug('Waiting for disk deletion.')
            time.sleep(POLL_INTERVAL)
            retry_count += 1

    if retry_count == nebius.MAX_RETRIES_TO_DISK_DELETE:
        raise TimeoutError(
            f'Exceeded maximum retries '
            f'({nebius.MAX_RETRIES_TO_DISK_DELETE * POLL_INTERVAL}'
            f' seconds) while waiting for disk {disk_id}'
            f' to be deleted.')
