"""Test Lambda Cloud filesystem volumes."""
import json
from unittest import mock

import pytest

from sky import clouds
from sky import models
from sky.provision.lambda_cloud import instance as lambda_instance
from sky.provision.lambda_cloud import lambda_utils
from sky.provision.lambda_cloud import volume as lambda_volume
from sky.utils import volume as volume_utils
from sky.volumes import volume as volume_lib

_REGION = 'us-west-3'
_FILESYSTEM = {
    'id': 'fs-123',
    'name': 'models-1c9eec68-ab12cd',
    'region': {
        'name': _REGION,
        'description': 'Utah, USA'
    },
}


@pytest.fixture(autouse=True)
def _no_catalog(monkeypatch):
    # Region validation reads the hosted catalog; keep tests offline.
    monkeypatch.setattr(clouds.Lambda, 'validate_region_zone',
                        lambda self, region, zone: (region, zone))


def _volume_config(**overrides) -> models.VolumeConfig:
    values = {
        'name': 'models',
        'type': volume_utils.VolumeType.LAMBDA_FILESYSTEM.value,
        'cloud': 'lambda',
        'region': _REGION,
        'zone': None,
        'name_on_cloud': _FILESYSTEM['name'],
        'size': None,
        'config': {},
    }
    values.update(overrides)
    return models.VolumeConfig(**values)


@pytest.fixture
def client(monkeypatch):
    fake = mock.MagicMock()
    fake.list_filesystems.return_value = [_FILESYSTEM]
    monkeypatch.setattr(lambda_utils, 'LambdaCloudClient', lambda: fake)
    return fake


class TestVolumeSpec:

    def _volume(self, **overrides):
        config = {
            'name': 'models',
            'type': 'lambda-filesystem',
            'infra': f'lambda/{_REGION}',
        }
        config.update(overrides)
        return volume_lib.Volume.from_yaml_config(config)

    def test_factory_returns_lambda_subclass(self):
        volume = self._volume()
        assert isinstance(volume, volume_lib.LambdaFilesystemVolume)
        assert volume.cloud.lower() == 'lambda'
        assert volume.region == _REGION
        volume.validate()

    def test_cloud_defaults_from_type(self):
        volume = volume_lib.Volume.from_components(name='models',
                                                   type='lambda-filesystem',
                                                   region=_REGION)
        assert volume.cloud == 'Lambda'
        volume.validate()

    def test_region_is_required(self):
        with pytest.raises(ValueError, match='region is required'):
            self._volume(infra='lambda').validate()

    def test_size_is_rejected(self):
        with pytest.raises(ValueError, match='no fixed size'):
            self._volume(size='100Gi').validate()

    def test_labels_are_rejected(self):
        with pytest.raises(ValueError, match='Labels'):
            self._volume(labels={'team': 'ml'}).validate()

    def test_other_cloud_is_rejected(self):
        with pytest.raises(ValueError, match='Invalid cloud'):
            self._volume(infra='runpod')

    @pytest.mark.parametrize('name,valid', [
        ('models', True),
        ('Models-2', True),
        ('2models', False),
        ('a' * 61, False),
    ])
    def test_name(self, name, valid):
        assert clouds.Lambda.is_volume_name_valid(name)[0] is valid


class TestApplyVolume:

    def test_reuses_an_existing_filesystem(self, client):
        config = lambda_volume.apply_volume(_volume_config())
        assert config.id_on_cloud == 'fs-123'
        client.create_filesystem.assert_not_called()

    def test_same_name_in_another_region_is_not_reused(self, client):
        client.create_filesystem.return_value = {'id': 'fs-new'}
        config = lambda_volume.apply_volume(_volume_config(region='us-east-1'))
        assert config.id_on_cloud == 'fs-new'
        client.create_filesystem.assert_called_once_with(
            _FILESYSTEM['name'], 'us-east-1')

    def test_creates_a_missing_filesystem(self, client):
        client.list_filesystems.return_value = []
        client.create_filesystem.return_value = {'id': 'fs-new'}
        config = lambda_volume.apply_volume(_volume_config())
        assert config.id_on_cloud == 'fs-new'
        client.create_filesystem.assert_called_once_with(
            _FILESYSTEM['name'], _REGION)

    def test_use_existing_never_creates(self, client):
        client.list_filesystems.return_value = []
        with pytest.raises(ValueError, match='does not exist'):
            lambda_volume.apply_volume(
                _volume_config(config={'use_existing': True}))
        client.create_filesystem.assert_not_called()

    @pytest.mark.parametrize('name', ['a' * 61, 'has_underscore', '1abc'])
    def test_invalid_name_on_cloud_is_refused_before_create(self, client, name):
        client.list_filesystems.return_value = []
        with pytest.raises(ValueError, match='Invalid Lambda filesystem name'):
            lambda_volume.apply_volume(_volume_config(name_on_cloud=name))
        client.create_filesystem.assert_not_called()


class TestDeleteVolume:

    def test_deletes_by_id(self, client):
        lambda_volume.delete_volume(_volume_config(id_on_cloud='fs-123'))
        client.delete_filesystem.assert_called_once_with('fs-123')
        client.list_filesystems.assert_not_called()

    def test_resolves_id_by_name_and_region(self, client):
        lambda_volume.delete_volume(_volume_config())
        client.delete_filesystem.assert_called_once_with('fs-123')

    def test_missing_filesystem_is_skipped(self, client):
        client.list_filesystems.return_value = []
        lambda_volume.delete_volume(_volume_config())
        client.delete_filesystem.assert_not_called()


class TestUsedBy:

    def _instances(self, client):
        client.list_instances.return_value = [
            {
                'name': 'sky-abc-1c9e-head',
                'status': 'active',
                'file_system_names': [_FILESYSTEM['name']],
            },
            {
                'name': 'other-head',
                'status': 'active',
                'file_system_names': ['unrelated'],
            },
            {
                'name': 'old-head',
                'status': 'terminated',
                'file_system_names': [_FILESYSTEM['name']],
            },
        ]

    def test_volume_usedby(self, client, monkeypatch):
        self._instances(client)
        handle = mock.MagicMock(cluster_name_on_cloud='sky-abc-1c9e')
        monkeypatch.setattr(
            'sky.global_user_state.get_clusters', lambda: [{
                'name': 'abc',
                'handle': handle
            }, {
                'name': 'xyz',
                'handle': mock.MagicMock(cluster_name_on_cloud='sky-xyz')
            }])
        instances, clusters = lambda_volume.get_volume_usedby(_volume_config())
        assert instances == ['sky-abc-1c9e-head']
        assert clusters == ['abc']

    def test_all_volumes_usedby_lists_instances_once(self, client, monkeypatch):
        self._instances(client)
        monkeypatch.setattr('sky.global_user_state.get_clusters', lambda: [])
        configs = [
            _volume_config(),
            _volume_config(name='b', name_on_cloud='b-x')
        ]
        instances, clusters, failed = lambda_volume.get_all_volumes_usedby(
            configs)
        assert client.list_instances.call_count == 1
        assert instances == {
            _FILESYSTEM['name']: ['sky-abc-1c9e-head'],
            'b-x': []
        }
        assert clusters == {_FILESYSTEM['name']: [], 'b-x': []}
        assert not failed
        assert lambda_volume.map_all_volumes_usedby(
            instances, clusters, configs[0]) == (['sky-abc-1c9e-head'], [])

    def test_listing_failure_marks_all_failed(self, client):
        client.list_instances.side_effect = RuntimeError('boom')
        _, _, failed = lambda_volume.get_all_volumes_usedby([_volume_config()])
        assert failed == {'models'}


class TestLaunch:

    def test_file_system_mounts_from_node_config(self):
        node_config = {
            'InstanceType': 'gpu_1x_h100_pcie',
            'VolumeMounts': [{
                'VolumeNameOnCloud': _FILESYSTEM['name'],
                'VolumeIdOnCloud': 'fs-123',
                'MountPath': '/data/models',
            }],
        }
        assert lambda_instance._file_system_mounts(node_config) == [{  # pylint: disable=protected-access
            'file_system_id': 'fs-123',
            'mount_point': '/data/models',
        }]

    def test_no_volume_mounts(self):
        assert lambda_instance._file_system_mounts(  # pylint: disable=protected-access
            {'InstanceType': 'gpu_1x_a10'}) is None

    def test_incomplete_volume_mount_is_refused(self):
        with pytest.raises(ValueError, match='filesystem ID and mount path'):
            lambda_instance._file_system_mounts({  # pylint: disable=protected-access
                'VolumeMounts': [{
                    'VolumeIdOnCloud': None,
                    'MountPath': '/data/models'
                }]
            })

    @pytest.mark.parametrize('mounts,expected', [
        (None, None),
        ([{
            'file_system_id': 'fs-123',
            'mount_point': '/data/models'
        }], [{
            'file_system_id': 'fs-123',
            'mount_point': '/data/models'
        }]),
    ])
    def test_launch_payload(self, monkeypatch, mounts, expected):
        monkeypatch.setattr(lambda_utils.LambdaCloudClient, '__init__',
                            lambda self: setattr(self, 'headers', {}))
        monkeypatch.setattr(
            lambda_utils.LambdaCloudClient, 'list_catalog', lambda self: {
                'gpu_1x_a10': {
                    'regions_with_capacity_available': [{
                        'name': _REGION
                    }]
                }
            })
        response = mock.MagicMock()
        response.json.return_value = {'data': {'instance_ids': ['i-1']}}
        with mock.patch.object(lambda_utils,
                               '_try_request_with_backoff',
                               return_value=response) as request:
            ids = lambda_utils.LambdaCloudClient().create_instances(
                instance_type='gpu_1x_a10',
                region=_REGION,
                name='c-head',
                ssh_key_name='key',
                file_system_mounts=mounts)
        assert ids == ['i-1']
        payload = json.loads(request.call_args.kwargs['data'])
        assert payload.get('file_system_mounts') == expected
        assert 'file_system_names' not in payload


class TestMountValidation:

    def _mount(self,
               path='/data/models',
               volume_type='lambda-filesystem',
               sub_path=None):
        return volume_utils.VolumeMount(
            path=path,
            volume_name='models',
            volume_config=_volume_config(type=volume_type),
            sub_path=sub_path)

    @pytest.mark.parametrize('path', [
        '/data/models',
        '/home/ubuntu/models',
        '/lambda/nfs/models',
    ])
    def test_allowed_paths(self, path):
        clouds.Lambda._validate_volume_mounts([self._mount(path)])  # pylint: disable=protected-access

    @pytest.mark.parametrize('path', ['/mnt/models', '/workspace', '/data/a b'])
    def test_disallowed_paths(self, path):
        with pytest.raises(ValueError, match='/home, /data or /lambda/nfs'):
            clouds.Lambda._validate_volume_mounts([self._mount(path)])  # pylint: disable=protected-access

    def test_other_volume_types_are_refused(self):
        with pytest.raises(ValueError, match='only .lambda-filesystem.'):
            clouds.Lambda._validate_volume_mounts(  # pylint: disable=protected-access
                [self._mount(volume_type='runpod-network-volume')])

    def test_sub_path_is_refused(self):
        with pytest.raises(ValueError, match='sub_path'):
            clouds.Lambda._validate_volume_mounts(  # pylint: disable=protected-access
                [self._mount(sub_path='a')])
