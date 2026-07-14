"""Unit tests for the OpenStack provisioner."""

import base64
import importlib
import types

import pytest

from sky.provision import common
from sky.provision import constants
from sky.utils import status_lib


class _Resource:
    """Minimal resource with attribute and mapping-style field access."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def get(self, key, default=None):
        return getattr(self, key, default)


class _FakeImage:
    """In-memory Glance proxy."""

    def __init__(self):
        self.images = {
            'ubuntu': _Resource(id='image-id', name='ubuntu'),
            'image-id': _Resource(id='image-id', name='ubuntu'),
        }

    def find_image(self, image, ignore_missing=True):
        del ignore_missing
        return self.images.get(image)


class _FakeNetwork:
    """In-memory Neutron proxy."""

    def __init__(self, connection):
        self._connection = connection
        self.networks = {
            'tenant-net': _Resource(id='network-id', name='tenant-net'),
            'network-id': _Resource(id='network-id', name='tenant-net'),
            'public': _Resource(id='public-id', name='public'),
            'public-id': _Resource(id='public-id', name='public'),
        }
        self.security_group_items = []
        self.security_group_rule_items = []
        self.port_items = []
        self.floating_ips = []
        self.deleted_security_groups = []
        self.deleted_ips = []
        self.reject_inline_tags = False
        self.set_tags_calls = []
        self.raise_on_associate = False

    def find_network(self, network, ignore_missing=True):
        del ignore_missing
        return self.networks.get(network)

    def find_security_group(self, security_group, ignore_missing=True):
        del ignore_missing
        for item in self.security_group_items:
            if item.id == security_group or item.name == security_group:
                return item
        return None

    def security_groups(self, **kwargs):
        name = kwargs.get('name')
        return [
            item for item in self.security_group_items
            if name is None or item.name == name
        ]

    def create_security_group(self, **kwargs):
        if self.reject_inline_tags and 'tags' in kwargs:
            raise ValueError('inline tags are not supported')
        item = _Resource(id=f'sg-{len(self.security_group_items) + 1}',
                         project_id=self._connection.current_project_id,
                         **kwargs)
        self.security_group_items.append(item)
        return item

    def set_tags(self, resource, tags):
        resource.tags = list(tags)
        self.set_tags_calls.append((resource.id, list(tags)))
        return resource

    def security_group_rules(self, **kwargs):
        security_group_id = kwargs.get('security_group_id')
        return [
            rule for rule in self.security_group_rule_items
            if rule.security_group_id == security_group_id
        ]

    def create_security_group_rule(self, **kwargs):
        rule = _Resource(id=f'rule-{len(self.security_group_rule_items) + 1}',
                         **kwargs)
        self.security_group_rule_items.append(rule)
        return rule

    def delete_security_group(self, security_group, ignore_missing=True):
        del ignore_missing
        security_group_id = getattr(security_group, 'id', security_group)
        self.deleted_security_groups.append(security_group_id)
        self.security_group_items = [
            item for item in self.security_group_items
            if item.id != security_group_id
        ]

    def ports(self, **kwargs):
        device_id = kwargs.get('device_id')
        network_id = kwargs.get('network_id')
        return [
            port for port in self.port_items
            if (device_id is None or port.device_id == device_id) and
            (network_id is None or port.network_id == network_id)
        ]

    def create_ip(self, **kwargs):
        if self.reject_inline_tags and 'tags' in kwargs:
            raise ValueError('inline tags are not supported')
        item = _Resource(id=f'fip-{len(self.floating_ips) + 1}',
                         floating_ip_address='203.0.113.10',
                         port_id=None,
                         project_id=self._connection.current_project_id,
                         **kwargs)
        self.floating_ips.append(item)
        return item

    def update_ip(self, floating_ip, **kwargs):
        if self.raise_on_associate:
            raise RuntimeError('floating IP association failed')
        floating_ip.port_id = kwargs['port_id']
        port = next(
            port for port in self.port_items if port.id == floating_ip.port_id)
        server = self._connection.compute.get_server(port.device_id)
        server.addresses[port.network_name].append({
            'addr': floating_ip.floating_ip_address,
            'version': 4,
            'OS-EXT-IPS:type': 'floating',
        })
        return floating_ip

    def ips(self, **kwargs):
        port_id = kwargs.get('port_id')
        return [
            item for item in self.floating_ips
            if port_id is None or item.port_id == port_id
        ]

    def delete_ip(self, floating_ip, ignore_missing=True):
        del ignore_missing
        floating_ip_id = getattr(floating_ip, 'id', floating_ip)
        self.deleted_ips.append(floating_ip_id)
        self.floating_ips = [
            item for item in self.floating_ips if item.id != floating_ip_id
        ]


class _FakeCompute:
    """In-memory Nova proxy."""

    def __init__(self, connection):
        self._connection = connection
        self.server_items = []
        self.flavors = {
            'm1.small': _Resource(id='flavor-id', name='m1.small'),
            'flavor-id': _Resource(id='flavor-id', name='m1.small'),
        }
        self.create_server_calls = []
        self.started = []
        self.stopped = []
        self.deleted_servers = []

    def servers(self, **kwargs):
        del kwargs
        return list(self.server_items)

    def get_server(self, server):
        server_id = getattr(server, 'id', server)
        return next(item for item in self.server_items if item.id == server_id)

    def find_flavor(self, flavor, ignore_missing=True):
        del ignore_missing
        return self.flavors.get(flavor)

    def create_server(self, **kwargs):
        self.create_server_calls.append(kwargs)
        server = _Resource(id=f'server-{len(self.server_items) + 1}',
                           name=kwargs['name'],
                           project_id=self._connection.current_project_id,
                           status='BUILD',
                           metadata=dict(kwargs['metadata']),
                           fault=None,
                           addresses={
                               'tenant-net': [{
                                   'addr': '10.0.0.10',
                                   'version': 4,
                                   'OS-EXT-IPS:type': 'fixed',
                               }]
                           })
        self.server_items.append(server)
        network_id = kwargs['networks'][0]['uuid']
        self._connection.network.port_items.append(
            _Resource(id=f'port-{server.id}',
                      device_id=server.id,
                      network_id=network_id,
                      network_name='tenant-net',
                      fixed_ips=[{
                          'ip_address': '10.0.0.10',
                      }]))
        return server

    def wait_for_server(self, server, **kwargs):
        server = self.get_server(server)
        server.status = kwargs.get('status', 'ACTIVE')
        return server

    def start_server(self, server):
        server = self.get_server(server)
        self.started.append(server.id)
        server.status = 'ACTIVE'

    def stop_server(self, server):
        server = self.get_server(server)
        self.stopped.append(server.id)
        server.status = 'SHUTOFF'

    def delete_server(self, server, ignore_missing=True):
        del ignore_missing
        server = self.get_server(server)
        self.deleted_servers.append(server.id)
        server.status = 'DELETED'

    def wait_for_delete(self, server, **kwargs):
        del kwargs
        server_id = getattr(server, 'id', server)
        self.server_items = [
            item for item in self.server_items if item.id != server_id
        ]


class _FakeConnection:
    """Connection composed from in-memory service proxies."""

    def __init__(self):
        self.current_project_id = 'project-a'
        self.image = _FakeImage()
        self.network = _FakeNetwork(self)
        self.compute = _FakeCompute(self)


@pytest.fixture(name='modules')
def _modules_fixture():
    package = importlib.import_module('sky.provision.openstack')
    return types.SimpleNamespace(
        package=package,
        config=importlib.import_module('sky.provision.openstack.config'),
        instance=importlib.import_module('sky.provision.openstack.instance'),
        utils=importlib.import_module('sky.provision.openstack.utils'))


def _provision_config(*,
                      use_internal_ips=False,
                      security_group_name=None,
                      resume_stopped_nodes=True):
    provider_config = {
        'cloud': 'lab',
        'region': 'RegionOne',
        'network': 'tenant-net',
        'external_network': 'public',
        'use_internal_ips': use_internal_ips,
    }
    if security_group_name is not None:
        provider_config['security_group_name'] = security_group_name
    return common.ProvisionConfig(provider_config=provider_config,
                                  authentication_config={},
                                  docker_config={},
                                  node_config={
                                      'InstanceType': 'm1.small',
                                      'ImageId': 'ubuntu',
                                      'DiskSize': 20,
                                      'AuthorizedKey': 'ssh-rsa test-key',
                                      'AvailabilityZone': 'nova',
                                  },
                                  count=1,
                                  tags={'purpose': 'test'},
                                  resume_stopped_nodes=resume_stopped_nodes,
                                  ports_to_open_on_launch=None)


def _install_connection(monkeypatch, modules, connection):
    monkeypatch.setattr(modules.utils,
                        'get_connection',
                        lambda provider_config=None, region=None: connection)


def _bootstrap(monkeypatch, modules, connection, config=None):
    if config is None:
        config = _provision_config()
    _install_connection(monkeypatch, modules, connection)
    return modules.config.bootstrap_instances('RegionOne', 'demo', config)


def test_connection_falls_back_to_selected_profile(monkeypatch, modules):
    calls = []
    monkeypatch.setattr(modules.utils.skypilot_config, 'get_nested',
                        lambda **_kwargs: 'lab')
    monkeypatch.setattr(
        modules.utils.openstack_adaptor, 'get_connection',
        lambda cloud, region: calls.append((cloud, region)) or object())

    modules.utils.get_connection({'region': 'RegionOne'}, 'RegionOne')

    assert calls == [('lab', 'RegionOne')]


def test_connection_rejects_missing_named_profile(monkeypatch, modules):
    monkeypatch.setattr(modules.utils.skypilot_config, 'get_nested',
                        lambda **_kwargs: None)

    with pytest.raises(ValueError, match='named clouds.yaml profile'):
        modules.utils.get_connection({'region': 'RegionOne'}, 'RegionOne')


def test_bootstrap_creates_managed_network_resources(monkeypatch, modules):
    connection = _FakeConnection()

    config = _bootstrap(monkeypatch, modules, connection)

    assert config.node_config['NetworkId'] == 'network-id'
    assert config.node_config['SecurityGroupId'] == 'sg-1'
    user_data = base64.b64decode(config.node_config['UserData']).decode()
    assert user_data == ('#cloud-config\nssh_authorized_keys:\n'
                         '  - "ssh-rsa test-key"\n')
    assert config.provider_config['_sky_security_group'] == {
        'id': 'sg-1',
        'name': 'skypilot-demo',
        'managed_by_skypilot': True,
        'created': True,
    }
    assert len(connection.network.security_group_rule_items) == 2
    assert set(connection.network.security_group_items[0].tags) == set(
        modules.utils.managed_resource_tags('demo', 'security-group'))


def test_bootstrap_tags_managed_security_group_after_creation(
        monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.reject_inline_tags = True

    _bootstrap(monkeypatch, modules, connection)

    assert connection.network.set_tags_calls == [
        ('sg-1', modules.utils.managed_resource_tags('demo', 'security-group'))
    ]


def test_bootstrap_recovers_interrupted_untagged_security_group(
        monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.security_group_items.append(
        _Resource(id='user-sg',
                  name=modules.utils.managed_security_group_name('demo'),
                  description=modules.utils.security_group_description('demo'),
                  project_id='project-a',
                  tags=[]))

    config = _bootstrap(monkeypatch, modules, connection)

    assert config.node_config['SecurityGroupId'] == 'user-sg'
    assert set(connection.network.security_group_items[0].tags) == set(
        modules.utils.managed_resource_tags('demo', 'security-group'))
    assert len(connection.network.security_group_rule_items) == 2


def test_bootstrap_adds_broad_self_rule_when_narrow_rule_exists(
        monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.security_group_items.append(
        _Resource(id='managed-sg',
                  name=modules.utils.managed_security_group_name('demo'),
                  description=modules.utils.security_group_description('demo'),
                  project_id='project-a',
                  tags=modules.utils.managed_resource_tags(
                      'demo', 'security-group')))
    connection.network.security_group_rule_items.append(
        _Resource(id='narrow-self-rule',
                  security_group_id='managed-sg',
                  direction='ingress',
                  ether_type='IPv4',
                  protocol='tcp',
                  port_range_min=22,
                  port_range_max=22,
                  remote_ip_prefix=None,
                  remote_group_id='managed-sg'))

    _bootstrap(monkeypatch, modules, connection)

    broad_self_rules = [
        rule for rule in connection.network.security_group_rule_items
        if rule.security_group_id == 'managed-sg' and
        rule.direction == 'ingress' and rule.ether_type == 'IPv4' and
        getattr(rule, 'protocol', None) is None and
        getattr(rule, 'port_range_min', None) is None and
        getattr(rule, 'port_range_max', None) is None and
        getattr(rule, 'remote_ip_prefix', None) is None and
        getattr(rule, 'remote_group_id', None) == 'managed-sg'
    ]
    assert len(broad_self_rules) == 1


def test_bootstrap_keeps_exact_security_group_rules_idempotent(
        monkeypatch, modules):
    connection = _FakeConnection()

    _bootstrap(monkeypatch, modules, connection)
    _bootstrap(monkeypatch, modules, connection)

    assert len(connection.network.security_group_rule_items) == 2


def test_bootstrap_does_not_mutate_byo_security_group(monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.security_group_items.append(
        _Resource(id='byo-id',
                  name='locked-down',
                  project_id='project-a',
                  description='user owned'))
    config = _provision_config(security_group_name='locked-down')

    config = _bootstrap(monkeypatch, modules, connection, config)

    assert config.node_config['SecurityGroupId'] == 'byo-id'
    assert config.provider_config['_sky_security_group'] == {
        'id': 'byo-id',
        'name': 'locked-down',
        'managed_by_skypilot': False,
        'created': False,
    }
    assert not connection.network.security_group_rule_items


def test_bootstrap_preserves_byo_security_group_id_for_nova(
        monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.security_group_items.append(
        _Resource(id='byo-id',
                  name='locked-down',
                  project_id='project-a',
                  description='user owned'))
    config = _provision_config(security_group_name='byo-id')

    config = _bootstrap(monkeypatch, modules, connection, config)
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert config.provider_config['_sky_security_group'][
        'name'] == 'locked-down'
    assert connection.compute.create_server_calls[0]['security_groups'] == [{
        'name': 'locked-down'
    }]


def test_bootstrap_rejects_byo_security_group_from_other_project(
        monkeypatch, modules):
    connection = _FakeConnection()
    connection.network.security_group_items.append(
        _Resource(id='other-id',
                  name='other-project-sg',
                  project_id='project-b',
                  description='user owned'))

    with pytest.raises(ValueError, match='not in the selected project'):
        _bootstrap(monkeypatch, modules, connection,
                   _provision_config(security_group_name='other-project-sg'))


def test_bootstrap_rejects_unknown_network(monkeypatch, modules):
    connection = _FakeConnection()
    config = _provision_config()
    config.provider_config['network'] = 'missing'
    _install_connection(monkeypatch, modules, connection)

    with pytest.raises(ValueError, match='network.*missing'):
        modules.config.bootstrap_instances('RegionOne', 'demo', config)


def test_bootstrap_rejects_multi_node_before_creating_resources(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _provision_config()
    config.count = 2
    _install_connection(monkeypatch, modules, connection)

    with pytest.raises(ValueError, match='single-node'):
        modules.config.bootstrap_instances('RegionOne', 'demo', config)

    assert not connection.network.security_group_items


def test_run_instances_launches_direct_image_boot_and_floating_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)

    record = modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert record.head_instance_id == 'server-1'
    assert record.created_instance_ids == ['server-1']
    assert not record.resumed_instance_ids
    create = connection.compute.create_server_calls[0]
    assert create['image_id'] == 'image-id'
    assert create['flavor_id'] == 'flavor-id'
    assert create['networks'] == [{'uuid': 'network-id'}]
    assert 'key_name' not in create
    assert base64.b64decode(create['user_data']).decode().startswith(
        '#cloud-config\nssh_authorized_keys:')
    assert create['availability_zone'] == 'nova'
    assert create['metadata'][constants.TAG_SKYPILOT_CLUSTER_NAME] == 'demo'
    assert create['metadata'][constants.TAG_SKYPILOT_HEAD_NODE] == '1'
    assert connection.network.floating_ips[0].port_id == 'port-server-1'
    assert set(connection.network.floating_ips[0].tags) == set(
        modules.utils.managed_resource_tags('demo', 'floating-ip'))

    info = modules.instance.get_cluster_info('RegionOne', 'demo',
                                             config.provider_config)
    head = info.get_head_instance()
    assert head.internal_ip == '10.0.0.10'
    assert head.external_ip == '203.0.113.10'
    assert head.node_name == 'demo-head'


def test_run_instances_tags_floating_ip_after_creation(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    connection.network.reject_inline_tags = True

    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert connection.network.set_tags_calls[-1] == (
        'fip-1', modules.utils.managed_resource_tags('demo', 'floating-ip'))


def test_run_instances_uses_internal_ip_without_floating_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection,
                        _provision_config(use_internal_ips=True))

    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert not connection.network.floating_ips
    info = modules.instance.get_cluster_info('RegionOne', 'demo',
                                             config.provider_config)
    head = info.get_head_instance()
    assert head.internal_ip == '10.0.0.10'
    assert head.external_ip is None


def test_get_cluster_info_skips_building_server_without_fixed_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    _install_connection(monkeypatch, modules, connection)
    connection.compute.server_items.append(
        _Resource(id='building',
                  name='demo-head',
                  project_id='project-a',
                  status='BUILD',
                  metadata=modules.utils.instance_metadata('demo'),
                  fault=None,
                  addresses={}))

    info = modules.instance.get_cluster_info(
        'RegionOne', 'demo',
        _provision_config().provider_config)

    assert not info.instances
    assert info.head_instance_id is None


def test_get_cluster_info_rejects_active_server_without_fixed_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    _install_connection(monkeypatch, modules, connection)
    connection.compute.server_items.append(
        _Resource(id='active',
                  name='demo-head',
                  project_id='project-a',
                  status='ACTIVE',
                  metadata=modules.utils.instance_metadata('demo'),
                  fault=None,
                  addresses={}))

    with pytest.raises(RuntimeError, match='no fixed IPv4 address'):
        modules.instance.get_cluster_info('RegionOne', 'demo',
                                          _provision_config().provider_config)


def test_get_cluster_info_reports_error_server_fault(monkeypatch, modules):
    connection = _FakeConnection()
    _install_connection(monkeypatch, modules, connection)
    connection.compute.server_items.append(
        _Resource(id='failed',
                  name='demo-head',
                  project_id='project-a',
                  status='ERROR',
                  metadata=modules.utils.instance_metadata('demo'),
                  fault={'message': 'No valid host'},
                  addresses={}))

    with pytest.raises(RuntimeError, match='No valid host'):
        modules.instance.get_cluster_info('RegionOne', 'demo',
                                          _provision_config().provider_config)


def test_public_mode_rejects_missing_external_network_before_vm_creation(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _provision_config()
    config.provider_config['external_network'] = 'missing-public'
    _install_connection(monkeypatch, modules, connection)

    with pytest.raises(ValueError, match='external network.*missing-public'):
        modules.config.bootstrap_instances('RegionOne', 'demo', config)

    assert not connection.compute.create_server_calls
    assert not connection.network.security_group_items


def test_run_instances_is_idempotent_for_active_node(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)

    first = modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
    second = modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert first.created_instance_ids == ['server-1']
    assert not second.created_instance_ids
    assert not second.resumed_instance_ids
    assert len(connection.compute.create_server_calls) == 1
    assert len(connection.network.floating_ips) == 1


def test_run_instances_reuses_owned_unattached_floating_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    connection.network.floating_ips.append(
        _Resource(id='orphan-fip',
                  floating_ip_address='203.0.113.20',
                  floating_network_id='public-id',
                  port_id=None,
                  project_id='project-a',
                  description=modules.utils.floating_ip_description('demo'),
                  tags=[]))

    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert [item.id for item in connection.network.floating_ips
           ] == ['orphan-fip']
    assert connection.network.floating_ips[0].port_id == 'port-server-1'
    assert set(connection.network.floating_ips[0].tags) == set(
        modules.utils.managed_resource_tags('demo', 'floating-ip'))


def test_run_instances_resumes_stopped_node(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    initial = modules.instance.run_instances('RegionOne', 'demo', 'demo',
                                             config)
    server = connection.compute.get_server(initial.head_instance_id)
    server.status = 'SHUTOFF'

    record = modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert not record.created_instance_ids
    assert record.resumed_instance_ids == ['server-1']
    assert connection.compute.started == ['server-1']


def test_run_instances_waits_for_building_node(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    metadata = modules.utils.instance_metadata('demo')
    connection.compute.server_items.append(
        _Resource(id='building',
                  name='demo-head',
                  project_id='project-a',
                  status='BUILD',
                  metadata=metadata,
                  fault=None,
                  addresses={
                      'tenant-net': [{
                          'addr': '10.0.0.10',
                          'version': 4,
                          'OS-EXT-IPS:type': 'fixed',
                      }]
                  }))
    connection.network.port_items.append(
        _Resource(id='port-building',
                  device_id='building',
                  network_id='network-id',
                  network_name='tenant-net',
                  fixed_ips=[{
                      'ip_address': '10.0.0.10',
                  }]))

    record = modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert record.head_instance_id == 'building'
    assert not record.created_instance_ids
    assert not connection.compute.create_server_calls
    assert connection.compute.get_server('building').status == 'ACTIVE'


def test_query_maps_status_and_preserves_error_fault(monkeypatch, modules):
    connection = _FakeConnection()
    _install_connection(monkeypatch, modules, connection)
    metadata = modules.utils.instance_metadata('demo')
    connection.compute.server_items.extend([
        _Resource(id='active',
                  name='active',
                  project_id='project-a',
                  status='ACTIVE',
                  metadata=metadata,
                  fault=None),
        _Resource(id='stopped',
                  name='stopped',
                  project_id='project-a',
                  status='SHUTOFF',
                  metadata=metadata,
                  fault=None),
        _Resource(id='building',
                  name='building',
                  project_id='project-a',
                  status='BUILD',
                  metadata=metadata,
                  fault=None),
        _Resource(id='error',
                  name='error',
                  project_id='project-a',
                  status='ERROR',
                  metadata=metadata,
                  fault={'message': 'No valid host'}),
        _Resource(id='foreign',
                  name='demo-copy',
                  status='ACTIVE',
                  metadata={},
                  fault=None),
    ])
    provider_config = _provision_config().provider_config

    statuses = modules.instance.query_instances('demo', 'demo', provider_config)

    assert statuses['active'] == (status_lib.ClusterStatus.UP, None)
    assert statuses['stopped'] == (status_lib.ClusterStatus.STOPPED, None)
    assert statuses['building'] == (status_lib.ClusterStatus.INIT, None)
    assert statuses['error'][0] == status_lib.ClusterStatus.INIT
    assert 'No valid host' in statuses['error'][1]
    assert 'foreign' not in statuses

    with pytest.raises(RuntimeError, match='No valid host'):
        modules.instance.wait_instances('RegionOne', 'demo',
                                        status_lib.ClusterStatus.UP)


def test_stop_instances_stops_only_owned_head(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
    connection.compute.server_items.append(
        _Resource(id='foreign',
                  name='demo-lookalike',
                  status='ACTIVE',
                  metadata={},
                  fault=None,
                  addresses={}))

    modules.instance.stop_instances('demo', config.provider_config)

    assert connection.compute.stopped == ['server-1']


def test_terminate_deletes_only_skypilot_owned_resources(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
    foreign_server = _Resource(id='foreign',
                               name='demo-lookalike',
                               status='ACTIVE',
                               metadata={},
                               fault=None,
                               addresses={})
    connection.compute.server_items.append(foreign_server)
    connection.network.floating_ips.append(
        _Resource(id='user-fip',
                  floating_ip_address='203.0.113.11',
                  port_id='port-server-1',
                  description='user owned'))

    modules.instance.terminate_instances('demo', config.provider_config)

    assert connection.compute.deleted_servers == ['server-1']
    assert connection.network.deleted_ips == ['fip-1']
    assert connection.network.deleted_security_groups == ['sg-1']
    assert connection.compute.server_items == [foreign_server]


def test_terminate_deletes_interrupted_description_only_floating_ip(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
    connection.network.floating_ips.append(
        _Resource(id='user-fip',
                  floating_ip_address='203.0.113.11',
                  port_id=None,
                  project_id='project-a',
                  description=modules.utils.floating_ip_description('demo'),
                  tags=[]))

    modules.instance.terminate_instances('demo', config.provider_config)

    assert connection.network.deleted_ips == ['fip-1', 'user-fip']
    assert not connection.network.floating_ips


def test_terminate_preserves_same_named_resources_from_other_project(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
    other_security_group = _Resource(
        id='other-sg',
        name=modules.utils.managed_security_group_name('demo'),
        project_id='project-b',
        description=modules.utils.security_group_description('demo'),
        tags=modules.utils.managed_resource_tags('demo', 'security-group'))
    connection.network.security_group_items.append(other_security_group)
    other_floating_ip = _Resource(
        id='other-fip',
        floating_ip_address='203.0.113.11',
        port_id=None,
        project_id='project-b',
        description=modules.utils.floating_ip_description('demo'),
        tags=modules.utils.managed_resource_tags('demo', 'floating-ip'))
    connection.network.floating_ips.append(other_floating_ip)
    other_server = _Resource(id='other-server',
                             name='other-head',
                             project_id='project-b',
                             status='ACTIVE',
                             metadata=modules.utils.instance_metadata('demo'),
                             fault=None,
                             addresses={})
    connection.compute.server_items.append(other_server)

    modules.instance.terminate_instances('demo', config.provider_config)

    assert connection.network.deleted_ips == ['fip-1']
    assert connection.network.floating_ips == [other_floating_ip]
    assert connection.network.deleted_security_groups == ['sg-1']
    assert connection.network.security_group_items == [other_security_group]
    assert connection.compute.server_items == [other_server]


def test_terminate_preserves_byo_security_group(monkeypatch, modules):
    connection = _FakeConnection()
    byo = _Resource(id='byo-id',
                    name='locked-down',
                    project_id='project-a',
                    description='user owned')
    connection.network.security_group_items.append(byo)
    config = _bootstrap(monkeypatch, modules, connection,
                        _provision_config(security_group_name='locked-down'))
    modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    modules.instance.terminate_instances('demo', config.provider_config)

    assert not connection.network.deleted_security_groups
    assert connection.network.security_group_items == [byo]


def test_run_failure_rolls_back_resources_created_by_attempt(
        monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    connection.network.raise_on_associate = True

    with pytest.raises(RuntimeError, match='association failed'):
        modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert connection.network.deleted_ips == ['fip-1']
    assert connection.compute.deleted_servers == ['server-1']
    assert connection.network.deleted_security_groups == ['sg-1']
    assert connection.network.networks['tenant-net'].id == 'network-id'


def test_run_rejects_missing_image(monkeypatch, modules):
    connection = _FakeConnection()
    config = _bootstrap(monkeypatch, modules, connection)
    config.node_config['ImageId'] = 'missing-image'

    with pytest.raises(ValueError, match='image.*missing-image'):
        modules.instance.run_instances('RegionOne', 'demo', 'demo', config)

    assert not connection.compute.create_server_calls


def test_run_rejects_multi_node(monkeypatch, modules):
    connection = _FakeConnection()
    config = _provision_config()
    config.count = 2
    _install_connection(monkeypatch, modules, connection)

    with pytest.raises(ValueError, match='single-node'):
        modules.instance.run_instances('RegionOne', 'demo', 'demo', config)
