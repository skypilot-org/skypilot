"""Tests for the UpCloud provisioner lifecycle."""
# pylint: disable=protected-access

import copy
import itertools
import shlex
import subprocess
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from sky import exceptions
from sky.provision import common
from sky.provision import instance_setup
from sky.provision.upcloud import config as upcloud_config
from sky.provision.upcloud import instance as upcloud_instance
from sky.provision.upcloud import utils
from sky.utils import status_lib

_ZONE = 'fi-hel2'
_NETWORK = '03000000-0000-4000-8000-000000000001'


class FakeUpCloudClient:
    """In-memory stand-in for utils.UpCloudClient."""

    def __init__(self):
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.storages: Dict[str, Dict[str, Any]] = {
            'tmpl-cpu': {
                'uuid': 'tmpl-cpu',
                'size': 4,
                'type': 'template'
            },
            'tmpl-gpu': {
                'uuid': 'tmpl-gpu',
                'size': 30,
                'type': 'template'
            },
        }
        self.groups: List[Dict[str, Any]] = []
        self.networks: List[Dict[str, Any]] = []
        self.firewall: Dict[str, List[Dict[str, Any]]] = {}
        self.created_bodies: List[Dict[str, Any]] = []
        self.calls: List[tuple] = []
        self.fail_delete: set = set()
        self._ids = itertools.count(1)

    def _labels(self, server):
        return utils.get_labels(server)

    def list_servers(self, cluster_name=None):
        servers = list(self.servers.values())
        if cluster_name is not None:
            servers = [
                s for s in servers
                if self._labels(s).get(utils.LABEL_CLUSTER) == cluster_name
            ]
        return [copy.deepcopy(s) for s in servers]

    def get_server(self, uuid):
        return copy.deepcopy(self.servers[uuid])

    def create_server(self, body):
        self.created_bodies.append(copy.deepcopy(body))
        uuid = f'srv-{next(self._ids)}'
        index = len(self.servers) + 1
        self.servers[uuid] = {
            'uuid': uuid,
            'hostname': body['hostname'],
            'plan': body['plan'],
            'state': 'started',
            'labels': body['labels'],
            'firewall': body['firewall'],
            'ip_addresses': {
                'ip_address': [{
                    'access': 'public',
                    'family': 'IPv4',
                    'address': f'203.0.113.{index}',
                }, {
                    'access': 'public',
                    'family': 'IPv6',
                    'address': f'2001:db8::{index}',
                }]
            },
            'networking': {
                'interfaces': {
                    'interface': [{
                        'type': 'public',
                        'ip_addresses': {
                            'ip_address': [{
                                'family': 'IPv4',
                                'address': f'203.0.113.{index}',
                            }]
                        },
                    }, {
                        'type': 'private',
                        'network': _NETWORK,
                        'ip_addresses': {
                            'ip_address': [{
                                'family': 'IPv4',
                                'address': f'10.100.0.{index}',
                            }]
                        },
                    }]
                }
            },
            'storage_devices': {
                'storage_device': [{
                    'storage': d.get('storage'),
                    'address': f'virtio:{i}',
                    'type': 'disk',
                } for i, d in enumerate(body['storage_devices']
                                        ['storage_device'])]
            },
        }
        return {'uuid': uuid}

    def start_server(self, uuid):
        self.calls.append(('start', uuid))
        self.servers[uuid]['state'] = 'started'

    def stop_server(self, uuid, stop_type='soft'):
        self.calls.append(('stop', uuid, stop_type))
        self.servers[uuid]['state'] = 'stopped'

    def delete_server(self, uuid):
        if uuid in self.fail_delete:
            raise utils.UpCloudError(f'cannot delete {uuid}')
        self.calls.append(('delete', uuid))
        del self.servers[uuid]

    def modify_server(self, uuid, body):
        self.calls.append(('modify', uuid, body))
        self.servers[uuid].update(body)

    def list_firewall_rules(self, uuid):
        return copy.deepcopy(self.firewall.get(uuid, []))

    def replace_firewall_rules(self, uuid, rules):
        self.calls.append(('firewall', uuid))
        self.firewall[uuid] = copy.deepcopy(rules)

    def list_server_groups(self, cluster_name=None):
        return [
            g for g in self.groups if cluster_name is None or
            utils.has_label(g, utils.LABEL_CLUSTER, cluster_name)
        ]

    def create_server_group(self, title, cluster_name):
        group = {
            'uuid': f'grp-{next(self._ids)}',
            'title': title,
            'labels': utils.cluster_labels(cluster_name),
        }
        self.groups.append(group)
        return group

    def delete_server_group(self, uuid):
        self.calls.append(('delete_group', uuid))
        self.groups = [g for g in self.groups if g['uuid'] != uuid]

    def list_networks(self, cluster_name=None, zone=None):
        return [
            n for n in self.networks
            if (cluster_name is None or
                utils.has_label(n, utils.LABEL_CLUSTER, cluster_name)) and
            (zone is None or n['zone'] == zone)
        ]

    def delete_network(self, uuid):
        self.calls.append(('delete_network', uuid))
        self.networks = [n for n in self.networks if n['uuid'] != uuid]

    def get_storage(self, uuid):
        return copy.deepcopy(self.storages[uuid])

    def detach_storage(self, server_uuid, address):
        self.calls.append(('detach', server_uuid, address))

    def list_public_templates(self):
        return [{
            'uuid': 'tmpl-cpu',
            'title': 'Ubuntu Server 24.04 LTS (Noble Numbat)',
        }, {
            'uuid': 'tmpl-gpu',
            'title': 'Ubuntu Server 24.04 LTS with NVIDIA drivers & CUDA',
        }]


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeUpCloudClient()
    monkeypatch.setattr(utils, 'UpCloudClient', lambda *a, **k: client)
    monkeypatch.setattr(utils.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(upcloud_instance.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(utils, 'read_public_key', lambda: 'ssh-key')
    utils.resolve_template_uuid.cache_clear()
    return client


def _config(count: int = 1,
            resume: bool = True,
            node_config: Optional[Dict[str, Any]] = None,
            ports: Optional[List[Any]] = None) -> common.ProvisionConfig:
    base = {
        'InstanceType': 'GPU-8xCPU-64GB-1xL40S',
        'UseSpot': False,
        'DiskSize': 256,
        'DiskTier': 'maxiops',
        'DefaultImage': 'gpu',
    }
    base.update(node_config or {})
    return common.ProvisionConfig(
        provider_config={
            'region': _ZONE,
            'network_uuid': _NETWORK
        },
        authentication_config={},
        docker_config={},
        node_config=base,
        count=count,
        tags={},
        resume_stopped_nodes=resume,
        ports_to_open_on_launch=ports,
    )


def _run(config, cluster='demo'):
    return upcloud_instance.run_instances(_ZONE, cluster, cluster, config)


def test_run_instances_single_node(fake_client):
    record = _run(_config())
    assert record.provider_name == 'upcloud'
    assert record.created_instance_ids == [record.head_instance_id]
    assert not record.resumed_instance_ids
    body = fake_client.created_bodies[0]
    assert body['hostname'] == 'demo-head'
    assert body['plan'] == 'GPU-8xCPU-64GB-1xL40S'
    assert body['firewall'] == 'on'
    assert 'server_group' not in body
    interfaces = body['networking']['interfaces']['interface']
    assert [i['type'] for i in interfaces] == ['public', 'private']
    assert interfaces[1]['network'] == _NETWORK
    device = body['storage_devices']['storage_device'][0]
    assert device['storage'] == 'tmpl-gpu'
    assert device['size'] == 256
    assert device['tier'] == 'maxiops'
    # Firewall chain applied; creation already enables the firewall.
    head = record.head_instance_id
    assert fake_client.firewall[head][-2] == {
        'direction': 'in',
        'action': 'drop',
        'comment': 'skypilot-default-in',
    }


def test_run_instances_spot_uses_spot_plan(fake_client):
    _run(_config(node_config={'UseSpot': True}))
    assert fake_client.created_bodies[0]['plan'] == (
        'GPU-SPOT-8xCPU-64GB-1xL40S')


def test_run_instances_multi_node(fake_client):
    record = _run(_config(count=3, ports=[8080, '9000-9010']))
    assert len(record.created_instance_ids) == 3
    hostnames = sorted(b['hostname'] for b in fake_client.created_bodies)
    assert hostnames == ['demo-head', 'demo-worker-1', 'demo-worker-2']
    groups = {b['server_group'] for b in fake_client.created_bodies}
    assert len(groups) == 1 and len(fake_client.groups) == 1
    roles = sorted([
        utils.get_labels(b)[utils.LABEL_ROLE]
        for b in fake_client.created_bodies
    ])
    assert roles == ['head', 'worker', 'worker']
    for uuid in record.created_instance_ids:
        assert utils.opened_port_ranges(fake_client.firewall[uuid]) == [
            (8080, 8080), (9000, 9010)
        ]


def test_run_instances_resumes_stopped(fake_client):
    first = _run(_config(count=2))
    upcloud_instance.stop_instances('demo')
    assert {s['state'] for s in fake_client.servers.values()} == {'stopped'}
    second = _run(_config(count=2))
    assert sorted(second.resumed_instance_ids) == sorted(
        first.created_instance_ids)
    assert not second.created_instance_ids
    assert second.head_instance_id == first.head_instance_id


def test_run_instances_no_resume_replaces_stopped(fake_client):
    first = _run(_config())
    upcloud_instance.stop_instances('demo')
    second = _run(_config(resume=False))
    assert ('delete', first.head_instance_id) in fake_client.calls
    assert not second.resumed_instance_ids
    assert second.created_instance_ids != first.created_instance_ids


def test_user_labels_applied(fake_client):
    _run(
        _config(
            node_config={'Labels': {
                'team': 'ml',
                utils.LABEL_ROLE: 'spoofed'
            }}))
    labels = utils.get_labels(fake_client.created_bodies[0])
    assert labels['team'] == 'ml'
    assert labels[utils.LABEL_CLUSTER] == 'demo'
    assert labels[utils.LABEL_ROLE] == 'head'


def test_terminate_instances_cleans_up(fake_client):
    _run(_config(count=2))
    fake_client.networks.append({
        'uuid': _NETWORK,
        'zone': _ZONE,
        'labels': utils.cluster_labels('demo'),
    })
    user_network = {'uuid': 'user-net', 'zone': _ZONE, 'labels': {}}
    fake_client.networks.append(user_network)
    upcloud_instance.terminate_instances('demo')
    assert fake_client.servers == {}
    assert not fake_client.groups
    assert fake_client.networks == [user_network]
    stops = [c for c in fake_client.calls if c[0] == 'stop']
    assert all(c[2] == 'hard' for c in stops)


def test_terminate_worker_only_keeps_head_and_network(fake_client):
    _run(_config(count=2))
    fake_client.networks.append({
        'uuid': _NETWORK,
        'zone': _ZONE,
        'labels': utils.cluster_labels('demo'),
    })
    upcloud_instance.terminate_instances('demo', worker_only=True)
    assert [s['hostname'] for s in fake_client.servers.values()
           ] == ['demo-head']
    assert len(fake_client.networks) == 1
    assert len(fake_client.groups) == 1


def test_query_instances(fake_client):
    record = _run(_config(count=2))
    fake_client.servers[record.created_instance_ids[1]]['state'] = (
        'maintenance')
    statuses = upcloud_instance.query_instances('demo', 'demo')
    assert statuses[record.head_instance_id] == (status_lib.ClusterStatus.UP,
                                                 None)
    assert statuses[record.created_instance_ids[1]] == (
        status_lib.ClusterStatus.INIT, None)


def test_get_cluster_info_uses_private_ip(fake_client):
    record = _run(_config(count=2))
    info = upcloud_instance.get_cluster_info(_ZONE, 'demo', {'region': _ZONE})
    assert info.head_instance_id == record.head_instance_id
    assert info.ssh_user == 'ubuntu'
    head = info.instances[record.head_instance_id][0]
    assert head.internal_ip.startswith('10.100.0.')
    assert head.external_ip.startswith('203.0.113.')
    assert head.tags[utils.LABEL_ROLE] == 'head'
    assert head.node_name == 'demo-head'


@pytest.mark.parametrize('provider', ['upcloud', 'aws'])
@pytest.mark.parametrize('no_restart', [False, True])
def test_ray_uses_upcloud_private_node_ips(fake_client, monkeypatch, tmp_path,
                                           provider, no_restart):
    _run(_config(count=3))
    cluster_info = upcloud_instance.get_cluster_info(_ZONE, 'demo', {})
    head = cluster_info.get_head_instance()
    instances = [head] + cluster_info.get_worker_instances()
    options = cluster_info.custom_ray_options
    if provider != 'upcloud':
        cluster_info.provider_name = provider
        cluster_info.custom_ray_options = None
    runners = [
        mock.Mock(run=mock.Mock(return_value=(0, '', ''))) for _ in instances
    ]
    monkeypatch.setattr(instance_setup.provision, 'get_command_runners',
                        lambda *args, **kwargs: runners)
    monkeypatch.setattr(instance_setup.provision_logging, 'get_log_path',
                        lambda: tmp_path / 'provision.log')
    monkeypatch.setattr(instance_setup, '_hint_worker_log_path', mock.Mock())
    monkeypatch.setattr(instance_setup.metadata_utils, 'get_instance_log_dir',
                        lambda *args: tmp_path)
    instance_setup.start_ray_on_head_node('demo', None, cluster_info, {})
    instance_setup.start_ray_on_worker_nodes('demo', no_restart, None, 6380,
                                             cluster_info, {})
    for node, runner in zip(instances, runners):
        command = runner.run.call_args.args[0]
        option = options['node-ip-address']
        assert (f'--node-ip-address={option}'
                in command) == (provider == 'upcloud')
        assert node.external_ip not in command
        if provider == 'upcloud':
            wrapper = (
                'import sys; from unittest import mock; '
                's = mock.Mock(); '
                f's.getsockname.return_value = ({node.internal_ip!r}, 12345); '
                'patch = mock.patch("socket.socket", return_value=s); '
                'patch.start(); exec(sys.argv[2]); '
                's.connect.assert_called_once_with('
                f'({head.internal_ip!r}, 9)); '
                's.close.assert_called_once_with()')
            script = ('python3() { '
                      f'command python3 -c {shlex.quote(wrapper)} "$@"; '
                      '}; '
                      f'printf "%s" {option}')
            assert subprocess.check_output(['bash', '-c', script],
                                           text=True) == node.internal_ip
    for runner in runners[1:]:
        command = runner.run.call_args.args[0]
        assert f'SKYPILOT_RAY_HEAD_IP="{head.internal_ip}"' in command


def _bootstrap(**provider):
    config = _config()
    config.provider_config = {'region': _ZONE, **provider}
    return upcloud_config.bootstrap_instances(_ZONE, 'demo', config)


def _add_network(client, uuid, name, zone=_ZONE, net_type='private'):
    client.networks.append({
        'uuid': uuid,
        'name': name,
        'zone': zone,
        'type': net_type
    })


def test_bootstrap_network_name_resolved_in_zone(fake_client):
    _add_network(fake_client, 'net-other-zone', 'my-net', zone='de-fra1')
    _add_network(fake_client, 'net-public', 'my-net', net_type='public')
    _add_network(fake_client, _NETWORK, 'my-net')
    config = _bootstrap(network_name='my-net')
    assert config.provider_config['network_uuid'] == _NETWORK


def test_bootstrap_network_name_missing_in_zone(fake_client):
    _add_network(fake_client, _NETWORK, 'my-net', zone='de-fra1')
    with pytest.raises(exceptions.ResourcesUnavailableError,
                       match='No UpCloud private network'):
        _bootstrap(network_name='my-net')


def test_bootstrap_network_name_ambiguous(fake_client):
    _add_network(fake_client, 'net-a', 'my-net')
    _add_network(fake_client, 'net-b', 'my-net')
    with pytest.raises(exceptions.ResourcesUnavailableError, match='Multiple'):
        _bootstrap(network_name='my-net')


def test_bootstrap_network_name_conflicts_with_volume(fake_client):
    _add_network(fake_client, _NETWORK, 'my-net')
    with pytest.raises(ValueError, match='file storage'):
        _bootstrap(network_name='my-net', network='net-volume')
    config = _bootstrap(network_name='my-net', network=_NETWORK)
    assert config.provider_config['network_uuid'] == _NETWORK
    assert _bootstrap(
        network='net-volume').provider_config['network_uuid'] == 'net-volume'


def _response(status: int) -> mock.Mock:
    return mock.Mock(status_code=status,
                     ok=status < 400,
                     content=b'{}',
                     json=lambda: {})


def test_request_retries_get_on_5xx_but_not_post(monkeypatch):
    monkeypatch.setattr(utils.time, 'sleep', lambda _: None)
    client = utils.UpCloudClient(token='token')
    client._session = mock.Mock()

    client._session.request.side_effect = [_response(503), _response(200)]
    assert client.get('/server') == {}
    assert client._session.request.call_count == 2

    client._session.request.reset_mock()
    client._session.request.side_effect = [_response(503), _response(200)]
    with pytest.raises(utils.UpCloudError, match='503'):
        client._request('POST', '/server', {})
    assert client._session.request.call_count == 1

    client._session.request.reset_mock()
    client._session.request.side_effect = [_response(429), _response(200)]
    assert client._request('POST', '/server', {}) == {}
    assert client._session.request.call_count == 2
