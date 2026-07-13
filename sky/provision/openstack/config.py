"""OpenStack configuration bootstrapping."""

import base64
import json
from typing import Any, Dict, List

from sky.provision import common
from sky.provision.openstack import utils


def _require_resource(resource: Any, kind: str, requested: str) -> Any:
    if resource is None:
        raise ValueError(f'OpenStack {kind} {requested!r} was not found.')
    return resource


def _same_rule(rule: Any, expected: Dict[str, Any]) -> bool:
    fields = ('direction', 'ether_type', 'protocol', 'port_range_min',
              'port_range_max', 'remote_ip_prefix', 'remote_group_id')
    return all(utils.get_attr(rule, key) == expected.get(key) for key in fields)


def _ensure_managed_security_group_rules(connection: Any,
                                         security_group_id: str) -> None:
    desired_rules: List[Dict[str, Any]] = [{
        'security_group_id': security_group_id,
        'direction': 'ingress',
        'ether_type': 'IPv4',
        'protocol': 'tcp',
        'port_range_min': 22,
        'port_range_max': 22,
        'remote_ip_prefix': '0.0.0.0/0',
    }, {
        'security_group_id': security_group_id,
        'direction': 'ingress',
        'ether_type': 'IPv4',
        'remote_group_id': security_group_id,
    }]
    existing_rules = list(
        connection.network.security_group_rules(
            security_group_id=security_group_id))
    for desired in desired_rules:
        if not any(_same_rule(rule, desired) for rule in existing_rules):
            connection.network.create_security_group_rule(**desired)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Resolves tenant resources and creates cluster-owned SSH resources."""
    if config.count != 1:
        raise ValueError('OpenStack MVP supports single-node clusters only.')
    connection = utils.get_connection(config.provider_config, region)
    provider_config = config.provider_config
    node_config = config.node_config

    network_name = provider_config['network']
    network = _require_resource(
        connection.network.find_network(network_name, ignore_missing=True),
        'network', network_name)

    external_network = None
    if not provider_config.get('use_internal_ips', False):
        external_network_name = provider_config.get('external_network')
        if not external_network_name:
            raise ValueError('OpenStack external network is required when '
                             '`use_internal_ips` is false.')
        external_network = _require_resource(
            connection.network.find_network(external_network_name,
                                            ignore_missing=True),
            'external network', external_network_name)

    byo_security_group_name = provider_config.get('security_group_name')
    security_group = None
    security_group_created = False
    try:
        if byo_security_group_name:
            security_group = _require_resource(
                connection.network.find_security_group(byo_security_group_name,
                                                       ignore_missing=True),
                'security group', byo_security_group_name)
            if not utils.is_current_project_resource(connection,
                                                     security_group):
                raise ValueError(
                    f'OpenStack security group {byo_security_group_name!r} '
                    'is not in the selected project.')
            security_group_name = utils.get_attr(security_group, 'name')
            if not security_group_name:
                raise ValueError('OpenStack security group does not report a '
                                 'name required by Nova.')
            managed_security_group = False
        else:
            security_group_name = utils.managed_security_group_name(
                cluster_name)
            security_group = utils.find_managed_security_group(
                connection, cluster_name)
            expected_description = utils.security_group_description(
                cluster_name)
            if security_group is None:
                security_group = connection.network.create_security_group(
                    name=security_group_name, description=expected_description)
                security_group_created = True
            elif (utils.get_attr(security_group, 'description') !=
                  expected_description):
                raise ValueError(
                    f'OpenStack security group {security_group_name!r} '
                    'already exists but is not owned by SkyPilot.')
            if not utils.has_managed_resource_tags(security_group, cluster_name,
                                                   'security-group'):
                # The description is written atomically with creation, so an
                # untagged exact match is a recoverable interrupted create.
                connection.network.set_tags(
                    security_group,
                    utils.managed_resource_tags(cluster_name, 'security-group'))
            _ensure_managed_security_group_rules(connection, security_group.id)
            managed_security_group = True

        public_key = node_config['AuthorizedKey'].strip()
        cloud_config = ('#cloud-config\nssh_authorized_keys:\n  - ' +
                        json.dumps(public_key) + '\n')

        security_group_id = utils.get_attr(security_group, 'id')
        node_config['NetworkId'] = utils.get_attr(network, 'id')
        node_config['SecurityGroupId'] = security_group_id
        node_config['UserData'] = base64.b64encode(
            cloud_config.encode()).decode()
        if external_network is not None:
            node_config['ExternalNetworkId'] = utils.get_attr(
                external_network, 'id')
        provider_config['_sky_security_group'] = {
            'id': security_group_id,
            'name': security_group_name,
            'managed_by_skypilot': managed_security_group,
            'created': security_group_created,
        }
        return config
    except Exception:
        if security_group_created and security_group is not None:
            connection.network.delete_security_group(security_group,
                                                     ignore_missing=True)
        raise
