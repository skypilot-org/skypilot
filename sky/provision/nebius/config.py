"""Nebius configuration bootstrapping."""

from sky import sky_logging
from sky.provision import common
from sky.provision.nebius import constants as nebius_constants
from sky.provision.nebius import utils

logger = sky_logging.init_logger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster.

    Creates (or looks up) a cluster-specific security group, ensures the
    default rule set is in place, and stashes the SG ID in `provider_config`
    and `node_config` so `run_instances` can attach it via
    `NetworkInterfaceSpec.security_groups` at VM creation time. This is what
    enforces the firewall before the VM ever boots Ray.

    `cluster_name` is `cluster_name_on_cloud` here (see
    `sky/provision/provisioner.py`).
    """
    project_id = utils.get_project_by_region(region)
    subnet_id = utils.get_subnet_id(region, project_id)
    network_id = utils.get_network_id_from_subnet(subnet_id)

    # Read the SG block emitted by `make_deploy_resources_variables` and
    # templated into `provider.security_group` of the Ray YAML. Defaults
    # cover the legacy code path where the template predates this feature.
    sg_block = config.provider_config.get('security_group') or {}
    sg_name = sg_block.get(
        'GroupName',
        nebius_constants.SECURITY_GROUP_TEMPLATE.format(cluster_name))
    managed = bool(sg_block.get('ManagedBySkyPilot', True))

    if managed:
        sg_id = utils.get_or_create_security_group(project_id, sg_name,
                                                   network_id)
        utils.ensure_default_sg_rules(sg_id)
    else:
        # BYO security group: look it up; error clearly if not found, in
        # a different network, or empty (no rules — would leave the
        # cluster unreachable). Single RPC returns both the SG id and
        # its network so we don't TOCTOU between two `get_by_name`s.
        lookup = utils.get_security_group_id_and_network_by_name(
            project_id, sg_name)
        if lookup is None:
            raise ValueError(
                f'Nebius security group {sg_name!r} (configured via '
                f'`nebius.security_group_name`) was not found in project '
                f'{project_id}. Either create it first, point at an '
                f'existing security group, or remove '
                f'`nebius.security_group_name` from your config to let '
                f'SkyPilot manage a security group for this cluster.')
        sg_id, sg_network_id = lookup
        if sg_network_id != network_id:
            raise ValueError(
                f'Nebius security group {sg_name!r} (network '
                f'{sg_network_id!r}) is not in this cluster\'s network '
                f'{network_id!r}. Cross-network NIC attachment is rejected '
                f'by Nebius VPC. To fix, either: (a) create a security '
                f'group named {sg_name!r} in network {network_id!r}, '
                f'(b) set `nebius.security_group_name` to a security group '
                f'that already exists in network {network_id!r}, or '
                f'(c) remove `nebius.security_group_name` to let SkyPilot '
                f'manage one.')
        # Diverge from AWS: do NOT seed default rules into a BYO SG. The
        # user opted into managing the rule set themselves; silently
        # adding SSH-from-anywhere would break that contract and could
        # turn an intentionally locked-down SG into an internet-exposed
        # cluster. Refuse the launch with an explanatory error if the
        # BYO SG is empty.
        if not utils.list_security_rules(sg_id):
            raise ValueError(
                f'Nebius security group {sg_name!r} has no rules. SkyPilot '
                f'will not seed default rules into a user-managed (BYO) '
                f'security group. Add the rules your cluster needs '
                f'(typically: SSH on 22 from your operator CIDR, '
                f'intra-cluster traffic via a self-referencing rule, '
                f'and any application ports) before launching, or remove '
                f'`nebius.security_group_name` to let SkyPilot manage a '
                f'security group for you.')

    # Detect pre-SG clusters: existing instances whose NICs lack our SG.
    # Nebius does not allow mutating a running NIC's security_groups, so we
    # cannot migrate them in place. Warn the user; new nodes added in this
    # provision (e.g. scale-up) will get the SG via NetworkInterfaceSpec.
    legacy = []
    for inst_id, info in utils.list_instances(project_id).items():
        name = info.get('name') or ''
        if not name.startswith(f'{cluster_name}-'):
            continue
        if sg_id not in info.get('security_group_ids', []):
            legacy.append(inst_id)
    if legacy:
        logger.warning(
            f'Cluster {cluster_name!r} has {len(legacy)} pre-existing '
            f'instance(s) without SkyPilot security group {sg_name!r}. '
            f'These instances were launched before network-level '
            f'firewalling was added and rely on host UFW rules. They '
            f'cannot be migrated in place. Run `sky down {cluster_name}` '
            f'and relaunch to fully migrate. New worker nodes added in '
            f'this provision will use the security group.')

    config.provider_config['security_group'] = {
        'GroupName': sg_name,
        'GroupId': sg_id,
        'ManagedBySkyPilot': managed,
    }
    config.node_config.setdefault('SecurityGroupIds', [sg_id])
    return config
