"""Tests for UpCloud File Storage volumes."""
# pylint: disable=protected-access

import ipaddress
from typing import Any, Dict, List
from unittest import mock

import pytest

from sky import clouds
from sky import models
from sky.provision.upcloud import utils
from sky.provision.upcloud import volume as upcloud_volume
from sky.utils import resources_utils
from sky.utils import volume as volume_lib
from sky.volumes import volume as volume_spec

_ZONE = 'fi-hel2'


class _FakeClient:

    def __init__(self):
        self.file_storages: List[Dict[str, Any]] = []
        self.networks: Dict[str, Dict[str, Any]] = {}
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.deleted: List[tuple] = []

    def create_network(self, name, zone, cidr, labels):
        uuid = f'net-{len(self.networks) + 1}'
        self.networks[uuid] = {
            'uuid': uuid,
            'name': name,
            'zone': zone,
            'ip_networks': {
                'ip_network': [{
                    'address': cidr,
                    'family': 'IPv4'
                }]
            },
            'labels': {
                'label': [{
                    'key': k,
                    'value': v
                } for k, v in labels.items()]
            },
            'servers': {
                'server': []
            },
        }
        return self.networks[uuid]

    def list_networks(self, zone=None):
        return [
            n for n in self.networks.values()
            if zone is None or n['zone'] == zone
        ]

    def get_network(self, uuid):
        return self.networks[uuid]

    def delete_network(self, uuid):
        self.deleted.append(('network', uuid))
        del self.networks[uuid]

    def create_file_storage(self, body):
        self.created_file_storage = body
        service = {
            'uuid': f'fs-{len(self.file_storages) + 1}',
            'name': body['name'],
            'zone': body['zone'],
            'size_gib': body['size_gib'],
            'operational_state': 'running',
            'labels': body['labels'],
            'networks': [{
                **body['networks'][0], 'ip_address': '10.101.0.2'
            }],
        }
        self.file_storages.append(service)
        return service

    def get_file_storage(self, uuid):
        return next(s for s in self.file_storages if s['uuid'] == uuid)

    def list_file_storages(self):
        return list(self.file_storages)

    def delete_file_storage(self, uuid):
        self.deleted.append(('file_storage', uuid))
        self.file_storages = [
            s for s in self.file_storages if s['uuid'] != uuid
        ]

    def get_server(self, uuid):
        return self.servers[uuid]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(utils, 'UpCloudClient', lambda *a, **k: client)
    monkeypatch.setattr(utils.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(upcloud_volume.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(upcloud_volume.global_user_state, 'get_clusters',
                        lambda: [])
    return client


def _volume_config(size: str = '250', **config) -> models.VolumeConfig:
    return models.VolumeConfig(
        name='data',
        type=volume_lib.VolumeType.UPCLOUD_FILE_STORAGE.value,
        cloud='upcloud',
        region=_ZONE,
        zone=None,
        name_on_cloud='data-abc123',
        size=size,
        config=config)


def test_apply_file_storage_skips_range_used_in_zone(fake_client):
    taken = utils.allocate_cidr('data-abc123', [])
    fake_client.create_network('other', _ZONE, taken, {})
    upcloud_volume.apply_volume(_volume_config())
    target = fake_client.created_file_storage['shares'][0]['acl'][0]['target']
    assert target != taken
    assert not ipaddress.ip_network(target).overlaps(
        ipaddress.ip_network(taken))


def test_apply_file_storage_creates_network_and_share(fake_client):
    config = upcloud_volume.apply_volume(_volume_config())
    body = fake_client.created_file_storage
    assert body['size_gib'] == 250
    assert body['configured_status'] == 'started'
    share = body['shares'][0]
    assert share['path'] == upcloud_volume.FILE_STORAGE_SHARE_PATH
    network = fake_client.networks['net-1']
    assert share['acl'][0]['target'] == utils.network_cidrs(network)[0]
    assert utils.has_label(network, utils.LABEL_VOLUME, 'data-abc123')
    assert not utils.has_label(network, utils.LABEL_CLUSTER, 'data-abc123')
    assert config.id_on_cloud == 'fs-1'
    assert config.config['nfs_address'] == '10.101.0.2'
    assert config.config['network'] == 'net-1'


def test_apply_file_storage_failure_deletes_network(fake_client):
    with mock.patch.object(fake_client,
                           'create_file_storage',
                           side_effect=utils.UpCloudError('quota')):
        with pytest.raises(utils.UpCloudError, match='quota'):
            upcloud_volume.apply_volume(_volume_config())
    assert fake_client.networks == {}
    assert fake_client.deleted == [('network', 'net-1')]


def test_delete_file_storage_deletes_own_network(fake_client):
    config = upcloud_volume.apply_volume(_volume_config())
    upcloud_volume.delete_volume(config)
    assert fake_client.deleted == [('file_storage', 'fs-1'),
                                   ('network', 'net-1')]


def test_apply_file_storage_reuses_and_syncs_size(fake_client):
    upcloud_volume.apply_volume(_volume_config())
    fake_client.file_storages[0]['size_gib'] = 500
    config = upcloud_volume.apply_volume(_volume_config())
    assert len(fake_client.file_storages) == 1
    assert config.size == '500'


def test_apply_file_storage_use_existing_missing(fake_client):
    del fake_client
    with pytest.raises(ValueError, match='use_existing'):
        upcloud_volume.apply_volume(_volume_config(use_existing=True))


def test_get_volume_usedby(fake_client, monkeypatch):
    config = upcloud_volume.apply_volume(_volume_config())
    fake_client.networks['net-1']['servers'] = {'server': [{'uuid': 'srv-1'}]}
    fake_client.servers['srv-1'] = {
        'uuid': 'srv-1',
        'hostname': 'demo-2ea4-head',
        'labels': utils.labels_as_list('demo-2ea4', 'head'),
    }
    handle = mock.Mock(cluster_name_on_cloud='demo-2ea4')
    monkeypatch.setattr(upcloud_volume.global_user_state, 'get_clusters',
                        lambda: [{
                            'name': 'demo',
                            'handle': handle
                        }])
    servers, clusters = upcloud_volume.get_volume_usedby(config)
    assert servers == ['demo-2ea4-head']
    assert clusters == ['demo']


def test_volume_min_size():
    with pytest.raises(ValueError, match='at least 250GB'):
        volume_spec.Volume.from_yaml_config({
            'name': 'data',
            'type': 'upcloud-file-storage',
            'infra': f'upcloud/{_ZONE}',
            'size': '100',
        }).validate(skip_cloud_compatibility=True)


def test_volume_requires_region():
    with pytest.raises(ValueError, match='region is required'):
        volume_spec.Volume.from_yaml_config({
            'name': 'data',
            'type': 'upcloud-file-storage',
            'infra': 'upcloud',
            'size': '250',
        }).validate(skip_cloud_compatibility=True)


def _mount(path: str, network: str = 'net-1'):
    volume_config = _volume_config(nfs_address='10.101.0.2',
                                   nfs_path='/skypilot',
                                   network=network)
    volume_config.id_on_cloud = 'fs-1'
    return volume_lib.VolumeMount(path, 'data', volume_config)


def test_volume_deploy_vars():
    files, network = clouds.UpCloud._volume_vars(_ZONE, [_mount('/shared')])
    assert files == [{
        'address': '10.101.0.2',
        'share_path': '/skypilot',
        'path': '/shared'
    }]
    assert network == 'net-1'
    assert clouds.UpCloud._volume_vars(_ZONE, None) == ([], None)


def test_volume_deploy_vars_rejects_two_networks():
    with pytest.raises(ValueError, match='one private network'):
        clouds.UpCloud._volume_vars(
            _ZONE, [_mount('/a', 'net-1'),
                    _mount('/b', 'net-2')])


def test_deploy_vars_volumes_with_docker_not_supported(monkeypatch):
    # pylint: disable=import-outside-toplevel
    from sky import exceptions
    from sky import resources as resources_lib
    monkeypatch.setattr(clouds.UpCloud, 'get_accelerators_from_instance_type',
                        classmethod(lambda cls, it: None))
    res = resources_lib.Resources(infra=f'upcloud/{_ZONE}',
                                  instance_type='2xCPU-4GB',
                                  image_id='docker:ubuntu:22.04')
    with pytest.raises(exceptions.NotSupportedError, match='docker'):
        clouds.UpCloud().make_deploy_resources_variables(
            res,
            resources_utils.ClusterName('c', 'c'),
            clouds.Region(_ZONE),
            None,
            1,
            volume_mounts=[_mount('/data')])
