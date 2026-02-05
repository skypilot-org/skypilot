"""Unit tests for sky.provision.volume."""
import copy
from typing import Any, Dict, Optional
from unittest import mock

import pytest

from sky import clouds
from sky import global_user_state
from sky import models
from sky.provision import common as provision_common
from sky.provision import volume as provision_volume
from sky.utils import volume as volume_utils

# ==================== Test Fixtures and Helper Functions ====================


def create_provision_config(provider_config: Optional[Dict[str, Any]] = None,
                            count: int = 1) -> provision_common.ProvisionConfig:
    """Create a ProvisionConfig with default values."""
    if provider_config is None:
        provider_config = {}
    return provision_common.ProvisionConfig(provider_config=provider_config,
                                            authentication_config={},
                                            docker_config={},
                                            node_config={},
                                            count=count,
                                            tags={},
                                            resume_stopped_nodes=False,
                                            ports_to_open_on_launch=None)


def create_volume_config(
        name: str = 'test-volume',
        volume_type: str = 'k8s-pvc',
        cloud: str = 'kubernetes',
        region: Optional[str] = 'us-central1',
        size: str = '100Gi',
        config: Optional[Dict[str, Any]] = None,
        labels: Optional[Dict[str, str]] = None,
        name_on_cloud: Optional[str] = None,
        id_on_cloud: Optional[str] = None) -> models.VolumeConfig:
    """Create a VolumeConfig with default values."""
    if config is None:
        config = {}
    if name_on_cloud is None:
        name_on_cloud = f'{name}-pvc'
    return models.VolumeConfig(_version=1,
                               name=name,
                               type=volume_type,
                               cloud=cloud,
                               region=region,
                               zone=None,
                               name_on_cloud=name_on_cloud,
                               size=size,
                               config=config,
                               labels=labels,
                               id_on_cloud=id_on_cloud)


def create_volume_config_dict(
        name: str = 'vol1',
        volume_type: str = 'k8s-pvc',
        size: str = '100Gi',
        config: Optional[Dict[str, Any]] = None,
        labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Create a volume_config dictionary for ephemeral_volume_specs."""
    if config is None:
        config = {}
    return {
        '_version': 1,
        'name': name,
        'type': volume_type,
        'cloud': 'kubernetes',
        'region': 'us-central1',
        'zone': None,
        'name_on_cloud': f'{name}-pvc',
        'size': size,
        'config': config,
        'labels': labels,
        'id_on_cloud': None
    }


def create_ephemeral_volume_spec(path: str,
                                 volume_name: str,
                                 volume_type: str = 'k8s-pvc',
                                 size: str = '100Gi') -> Dict[str, Any]:
    """Create an ephemeral volume spec dictionary."""
    return {
        'path': path,
        'volume_name': volume_name,
        'is_ephemeral': True,
        'volume_config': create_volume_config_dict(name=volume_name,
                                                   volume_type=volume_type,
                                                   size=size)
    }


@pytest.fixture
def mock_kubernetes_namespace(monkeypatch):
    """Mock kubernetes_utils.get_namespace_from_config."""
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.get_namespace_from_config',
        lambda x: 'default')


@pytest.fixture
def mock_k8s_label_validation(monkeypatch):
    """Mock Kubernetes label validation to always return valid."""
    monkeypatch.setattr('sky.clouds.kubernetes.Kubernetes.is_label_valid',
                        lambda self, key, value: (True, ''))


# ==================== Test Classes ====================


class TestResolveVolumeType:
    """Test cases for _resolve_volume_type function."""

    def test_resolve_volume_type_with_explicit_type(self):
        """Test resolving volume type when explicitly provided."""
        cloud = clouds.Kubernetes()
        volume_type = 'k8s-pvc'
        result = provision_volume._resolve_volume_type(cloud, volume_type)
        assert result == 'k8s-pvc'

    def test_resolve_volume_type_case_insensitive(self):
        """Test that volume type is case-insensitive."""
        cloud = clouds.Kubernetes()
        volume_type = 'K8s-PVC'
        result = provision_volume._resolve_volume_type(cloud, volume_type)
        assert result == 'k8s-pvc'

    def test_resolve_volume_type_default_single_type(self, monkeypatch):
        """Test resolving default volume type when cloud has single type."""
        cloud = clouds.Kubernetes()

        # Mock the CLOUD_TO_VOLUME_TYPE to return a single type
        mock_cloud_to_volume = {
            clouds.Kubernetes(): [volume_utils.VolumeType.PVC]
        }
        monkeypatch.setattr('sky.volumes.volume.CLOUD_TO_VOLUME_TYPE',
                            mock_cloud_to_volume)

        result = provision_volume._resolve_volume_type(cloud, None)
        assert result == 'k8s-pvc'

    def test_resolve_volume_type_no_default_found(self, monkeypatch):
        """Test error when no default volume type is found for cloud."""
        cloud = clouds.AWS()

        # Mock the CLOUD_TO_VOLUME_TYPE to not include AWS
        mock_cloud_to_volume = {
            clouds.Kubernetes(): [volume_utils.VolumeType.PVC]
        }
        monkeypatch.setattr('sky.volumes.volume.CLOUD_TO_VOLUME_TYPE',
                            mock_cloud_to_volume)

        with pytest.raises(ValueError, match='No default volume type found'):
            provision_volume._resolve_volume_type(cloud, None)

    def test_resolve_volume_type_multiple_types_error(self, monkeypatch):
        """Test error when cloud has multiple volume types and none specified."""
        cloud = clouds.Kubernetes()

        # Mock the CLOUD_TO_VOLUME_TYPE with multiple types
        mock_cloud_to_volume = {
            clouds.Kubernetes(): [
                volume_utils.VolumeType.PVC,
                volume_utils.VolumeType.RUNPOD_NETWORK_VOLUME
            ]
        }
        monkeypatch.setattr('sky.volumes.volume.CLOUD_TO_VOLUME_TYPE',
                            mock_cloud_to_volume)

        with pytest.raises(ValueError, match='Found multiple volume types'):
            provision_volume._resolve_volume_type(cloud, None)

    def test_resolve_volume_type_invalid_type(self):
        """Test error when invalid volume type is provided."""
        cloud = clouds.Kubernetes()
        volume_type = 'invalid-type'

        with pytest.raises(ValueError, match='Invalid volume type'):
            provision_volume._resolve_volume_type(cloud, volume_type)


class TestResolvePvcVolumeConfig:
    """Test cases for _resolve_pvc_volume_config function."""

    def test_resolve_pvc_volume_config_success(self, monkeypatch):
        """Test successful PVC volume config resolution."""
        cloud = clouds.Kubernetes()
        config = create_provision_config({'namespace': 'test-namespace'})
        volume_config = {'access_mode': 'ReadWriteOnce', 'size': '100Gi'}

        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_namespace_from_config',
            lambda x: 'test-namespace')

        result = provision_volume._resolve_pvc_volume_config(
            cloud, config, volume_config)

        assert result['access_mode'] == 'ReadWriteOnce'
        assert result['namespace'] == 'test-namespace'
        assert result['size'] == '100Gi'

    def test_resolve_pvc_volume_config_default_access_mode(self, monkeypatch):
        """Test default access mode is set when not provided."""
        cloud = clouds.Kubernetes()
        config = create_provision_config({'namespace': 'test-namespace'})
        volume_config = {'size': '100Gi'}

        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_namespace_from_config',
            lambda x: 'test-namespace')

        result = provision_volume._resolve_pvc_volume_config(
            cloud, config, volume_config)

        assert result['access_mode'] == 'ReadWriteOnce'

    def test_resolve_pvc_volume_config_invalid_access_mode(self, monkeypatch):
        """Test error when invalid access mode is provided."""
        cloud = clouds.Kubernetes()
        config = create_provision_config({'namespace': 'test-namespace'})
        volume_config = {'access_mode': 'InvalidMode'}

        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_namespace_from_config',
            lambda x: 'test-namespace')

        with pytest.raises(ValueError, match='Invalid access mode'):
            provision_volume._resolve_pvc_volume_config(cloud, config,
                                                        volume_config)

    def test_resolve_pvc_volume_config_non_kubernetes_cloud(self):
        """Test error when PVC config is used with non-Kubernetes cloud."""
        cloud = clouds.AWS()
        config = create_provision_config()
        volume_config = {}

        with pytest.raises(
                ValueError,
                match='PVC volume type is only supported on Kubernetes'):
            provision_volume._resolve_pvc_volume_config(cloud, config,
                                                        volume_config)

    def test_resolve_pvc_volume_config_rwo_multi_node(self, monkeypatch):
        """Test error when ReadWriteOnce is used with multi-node cluster."""
        cloud = clouds.Kubernetes()
        config = create_provision_config({'namespace': 'test-namespace'},
                                         count=3)
        volume_config = {'access_mode': 'ReadWriteOnce'}

        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_namespace_from_config',
            lambda x: 'test-namespace')

        with pytest.raises(
                ValueError,
                match='Access mode ReadWriteOnce is not supported for multi-node'
        ):
            provision_volume._resolve_pvc_volume_config(cloud, config,
                                                        volume_config)


class TestCreateEphemeralVolume:
    """Test cases for _create_ephemeral_volume function."""

    def test_create_ephemeral_volume_pvc_success(self, monkeypatch,
                                                 mock_kubernetes_namespace,
                                                 mock_k8s_label_validation):
        """Test successful creation of PVC ephemeral volume."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        config = create_provision_config({'namespace': 'default'})
        volume_config = create_volume_config(
            name='test-volume',
            cloud='Kubernetes',
            region=region,
            name_on_cloud='test-volume-cloud',
            config={'access_mode': 'ReadWriteMany'},
            labels={'env': 'test'},
            id_on_cloud='vol-12345')

        volume_mount = volume_utils.VolumeMount(path='/data',
                                                volume_name='test-volume',
                                                volume_config=volume_config,
                                                is_ephemeral=True)

        mock_volume_apply = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_apply',
                            mock_volume_apply)

        mock_get_volume = mock.MagicMock(return_value={
            'handle': volume_config,
            'name': 'test-volume'
        })
        monkeypatch.setattr('sky.global_user_state.get_volume_by_name',
                            mock_get_volume)

        result = provision_volume._create_ephemeral_volume(
            cloud, region, cluster_name, config, volume_mount)

        assert result is not None
        assert result.name == 'test-volume'
        assert result.path == '/data'
        assert result.volume_name_on_cloud == 'test-volume-cloud'
        assert result.volume_id_on_cloud == 'vol-12345'

        # Verify volume_apply was called with correct parameters
        mock_volume_apply.assert_called_once()
        call_kwargs = mock_volume_apply.call_args[1]
        assert call_kwargs['volume_type'] == 'k8s-pvc'
        assert call_kwargs['cloud'] == 'Kubernetes'
        assert call_kwargs['region'] == region
        assert call_kwargs['is_ephemeral'] is True

    def test_create_ephemeral_volume_invalid_labels(self, monkeypatch,
                                                    mock_kubernetes_namespace):
        """Test error when volume labels are invalid."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        config = create_provision_config({'namespace': 'default'})
        volume_config = create_volume_config(cloud='Kubernetes',
                                             region=region,
                                             labels={'invalid-key!': 'value'})

        volume_mount = volume_utils.VolumeMount(path='/data',
                                                volume_name='test-volume',
                                                volume_config=volume_config,
                                                is_ephemeral=True)

        # Mock invalid label validation
        monkeypatch.setattr(
            'sky.clouds.kubernetes.Kubernetes.is_label_valid',
            lambda self, key, value: (False, 'Invalid label key'))

        with pytest.raises(ValueError, match='Invalid label key'):
            provision_volume._create_ephemeral_volume(cloud, region,
                                                      cluster_name, config,
                                                      volume_mount)

    def test_create_ephemeral_volume_unsupported_type(self, monkeypatch):
        """Test that unsupported volume types return None."""
        cloud = clouds.AWS()
        region = 'us-east-1'
        cluster_name = 'test-cluster'

        config = create_provision_config()
        volume_config = create_volume_config(volume_type='ebs',
                                             cloud='AWS',
                                             region=region)

        volume_mount = volume_utils.VolumeMount(path='/data',
                                                volume_name='test-volume',
                                                volume_config=volume_config,
                                                is_ephemeral=True)

        # Mock to return a non-PVC type
        monkeypatch.setattr('sky.provision.volume._resolve_volume_type',
                            lambda cloud, vol_type: 'ebs')

        result = provision_volume._create_ephemeral_volume(
            cloud, region, cluster_name, config, volume_mount)

        assert result is None

    def test_create_ephemeral_volume_no_handle(self, monkeypatch,
                                               mock_kubernetes_namespace):
        """Test error when volume has no handle after creation."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        config = create_provision_config({'namespace': 'default'})
        volume_config = create_volume_config(cloud='Kubernetes', region=region)

        volume_mount = volume_utils.VolumeMount(path='/data',
                                                volume_name='test-volume',
                                                volume_config=volume_config,
                                                is_ephemeral=True)

        mock_volume_apply = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_apply',
                            mock_volume_apply)

        # Return None to simulate volume not found
        mock_get_volume = mock.MagicMock(return_value=None)
        monkeypatch.setattr('sky.global_user_state.get_volume_by_name',
                            mock_get_volume)

        with pytest.raises(ValueError, match='Failed to get record for volume'):
            provision_volume._create_ephemeral_volume(cloud, region,
                                                      cluster_name, config,
                                                      volume_mount)


class TestProvisionEphemeralVolumes:
    """Test cases for provision_ephemeral_volumes function."""

    def test_provision_ephemeral_volumes_success(self, monkeypatch):
        """Test successful provisioning of multiple ephemeral volumes."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        ephemeral_volume_specs = [
            create_ephemeral_volume_spec('/data1', 'vol1', size='100Gi'),
            create_ephemeral_volume_spec('/data2', 'vol2', size='200Gi'),
        ]

        provider_config = {
            'namespace': 'default',
            'ephemeral_volume_specs': ephemeral_volume_specs
        }
        config = create_provision_config(provider_config)

        # Mock _create_ephemeral_volume to return VolumeInfo
        def mock_create_volume(cloud, region, cluster_name, config,
                               volume_mount):
            return volume_utils.VolumeInfo(
                name=volume_mount.volume_config.name,
                path=volume_mount.path,
                volume_name_on_cloud=f'cloud-{volume_mount.path}',
                volume_id_on_cloud=f'id-{volume_mount.path}')

        monkeypatch.setattr('sky.provision.volume._create_ephemeral_volume',
                            mock_create_volume)

        provision_volume.provision_ephemeral_volumes(cloud, region,
                                                     cluster_name, config)

        # Check that volume infos were stored in provider_config
        assert 'ephemeral_volume_infos' in provider_config
        volume_infos = provider_config['ephemeral_volume_infos']
        assert len(volume_infos) == 2
        assert volume_infos[0].path == '/data1'
        assert volume_infos[1].path == '/data2'

    def test_provision_ephemeral_volumes_no_specs(self):
        """Test provisioning when no ephemeral volume specs are provided."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'
        provider_config = {}
        config = create_provision_config(provider_config)

        provision_volume.provision_ephemeral_volumes(cloud, region,
                                                     cluster_name, config)

        # No volume infos should be added when there are no specs
        assert 'ephemeral_volume_infos' not in provider_config

    def test_provision_ephemeral_volumes_skip_none(self, monkeypatch):
        """Test that None results from _create_ephemeral_volume are skipped."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        ephemeral_volume_specs = [
            create_ephemeral_volume_spec('/data1', 'vol1', size='100Gi'),
            create_ephemeral_volume_spec('/data2',
                                         'vol2',
                                         volume_type='unsupported',
                                         size='200Gi'),
        ]

        provider_config = {
            'namespace': 'default',
            'ephemeral_volume_specs': ephemeral_volume_specs
        }
        config = create_provision_config(provider_config)

        # Mock to return VolumeInfo for first, None for second
        call_count = [0]

        def mock_create_volume(cloud, region, cluster_name, config,
                               volume_mount):
            call_count[0] += 1
            if call_count[0] == 1:
                return volume_utils.VolumeInfo(
                    name='vol1',
                    path='/data1',
                    volume_name_on_cloud='cloud-vol-1',
                    volume_id_on_cloud='id-1')
            return None

        monkeypatch.setattr('sky.provision.volume._create_ephemeral_volume',
                            mock_create_volume)

        provision_volume.provision_ephemeral_volumes(cloud, region,
                                                     cluster_name, config)

        # Check that only the first volume was added (second returned None)
        assert 'ephemeral_volume_infos' in provider_config
        volume_infos = provider_config['ephemeral_volume_infos']
        assert len(volume_infos) == 1
        assert volume_infos[0].path == '/data1'

    def test_provision_ephemeral_volumes_preserves_original_config(
            self, monkeypatch):
        """Test that original volume specs are not modified."""
        cloud = clouds.Kubernetes()
        region = 'us-central1'
        cluster_name = 'test-cluster'

        ephemeral_volume_specs = [
            create_ephemeral_volume_spec('/data', 'vol1', size='100Gi')
        ]
        original_specs = copy.deepcopy(ephemeral_volume_specs)

        provider_config = {
            'namespace': 'default',
            'ephemeral_volume_specs': ephemeral_volume_specs
        }
        config = create_provision_config(provider_config)

        def mock_create_volume(cloud, region, cluster_name, config,
                               volume_mount):
            # Modify the volume_mount
            volume_mount.path = '/modified'
            return volume_utils.VolumeInfo(name='vol',
                                           path='/data',
                                           volume_name_on_cloud='cloud-vol',
                                           volume_id_on_cloud='id')

        monkeypatch.setattr('sky.provision.volume._create_ephemeral_volume',
                            mock_create_volume)

        provision_volume.provision_ephemeral_volumes(cloud, region,
                                                     cluster_name, config)

        # Original specs should be unchanged
        assert ephemeral_volume_specs == original_specs


class TestDeleteEphemeralVolumes:
    """Test cases for delete_ephemeral_volumes function."""

    def test_delete_ephemeral_volumes_success(self, monkeypatch):
        """Test successful deletion of ephemeral volumes."""
        ephemeral_volume_specs = [
            create_ephemeral_volume_spec('/data1', 'vol1', size='100Gi'),
            create_ephemeral_volume_spec('/data2', 'vol2', size='200Gi'),
        ]

        provider_config = {'ephemeral_volume_specs': ephemeral_volume_specs}

        mock_volume_delete = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_delete',
                            mock_volume_delete)

        provision_volume.delete_ephemeral_volumes(provider_config)

        # Verify volume_delete was called with correct names
        mock_volume_delete.assert_called_once()
        call_kwargs = mock_volume_delete.call_args[1]
        assert len(call_kwargs['names']) == 2
        assert call_kwargs['ignore_not_found'] is True

        # Volume names should match the names from the volume specs
        expected_names = ['vol1', 'vol2']
        assert set(call_kwargs['names']) == set(expected_names)

    def test_delete_ephemeral_volumes_no_specs(self, monkeypatch):
        """Test deletion when no ephemeral volume specs are provided."""
        provider_config = {}

        mock_volume_delete = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_delete',
                            mock_volume_delete)

        provision_volume.delete_ephemeral_volumes(provider_config)

        # volume_delete should not be called
        mock_volume_delete.assert_not_called()

    def test_delete_ephemeral_volumes_empty_specs(self, monkeypatch):
        """Test deletion when ephemeral volume specs list is empty."""
        provider_config = {'ephemeral_volume_specs': []}

        mock_volume_delete = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_delete',
                            mock_volume_delete)

        provision_volume.delete_ephemeral_volumes(provider_config)

        # volume_delete should not be called
        mock_volume_delete.assert_not_called()

    def test_delete_ephemeral_volumes_preserves_original_config(
            self, monkeypatch):
        """Test that original volume specs are not modified during deletion."""
        ephemeral_volume_specs = [
            create_ephemeral_volume_spec('/data', 'vol1', size='100Gi')
        ]
        original_specs = copy.deepcopy(ephemeral_volume_specs)

        provider_config = {'ephemeral_volume_specs': ephemeral_volume_specs}

        mock_volume_delete = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.volume_delete',
                            mock_volume_delete)

        provision_volume.delete_ephemeral_volumes(provider_config)

        # Original specs should be unchanged
        assert ephemeral_volume_specs == original_specs
