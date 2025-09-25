"""Tests for volume class."""

import pickle
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky import models
from sky.utils import common_utils
from sky.utils import schemas
from sky.volumes import volume as volume_lib


class TestVolume:

    def test_volume_adjust_config_valid_sizes(self):
        """Test Volume._adjust_config with valid sizes."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='1Gi')

        # Test various valid size formats
        test_cases = [
            {
                'input': '100Gi',
                'expected': '100'
            },
            {
                'input': '1Ti',
                'expected': '1024'
            },
            {
                'input': '100',
                'expected': '100'
            },
        ]

        for input_size in test_cases:
            volume.size = input_size['input']
            volume._adjust_config()  # Should not raise
            assert volume.size == input_size[
                'expected']  # Size should remain unchanged

    def test_volume_adjust_config_no_size(self):
        """Test Volume._adjust_config with no size."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   resource_name='test-pvc')
        volume._adjust_config()
        assert volume.size is None

    def test_volume_adjust_config_invalid_size(self):
        """Test Volume._adjust_config with invalid size."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='1Gi')

        # Test various valid size formats
        test_cases = ['50Mi', 'invalid', '0']

        for input_size in test_cases:
            volume.size = input_size
            with pytest.raises(ValueError) as exc_info:
                volume._adjust_config()
            assert 'Invalid size' in str(exc_info.value)

    def test_volume_adjust_config_edge_cases(self):
        """Test Volume._adjust_config with edge cases."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='1Gi')

        # Test with None size
        volume.size = None
        volume._adjust_config()
        assert volume.size is None

        # Test with empty string
        volume.size = ''
        with pytest.raises(ValueError):
            volume._adjust_config()

    def test_volume_validate_valid_with_size(self):
        """Test Volume.validate with valid size."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes')
        volume.validate()  # Should not raise

    def test_volume_validate_valid_with_resource_name(self):
        """Test Volume.validate with valid resource_name."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   infra='kubernetes',
                                   resource_name='existing-pvc')
        volume.validate()  # Should not raise

    def test_volume_validate_valid_with_both(self):
        """Test Volume.validate with both size and resource_name."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes',
                                   resource_name='existing-pvc')
        volume.validate()  # Should not raise

    def test_volume_validate_missing_size_and_resource_name(self):
        """Test Volume.validate with missing size and resource_name."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size=None)
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_validate_invalid_name(self):
        """Test Volume.validate with invalid name."""
        volume = volume_lib.Volume(name='test_xyz', type='k8s-pvc', size='1Gi')
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'Volume name must be a valid DNS-1123 subdomain' in str(
            exc_info.value)

    def test_volume_adjust_size(self):
        """Test integration of adjust and validate."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes')
        volume.validate()
        assert volume.size == '100'  # Size should be normalized

    def test_volume_validate_skip_cloud_compatibility(self, monkeypatch):
        """Test Volume.validate with skip_cloud_compatibility=True."""
        # Mock kubernetes_utils.get_all_kube_context_names to track if it's called
        mock_get_context_names = MagicMock()
        monkeypatch.setattr(
            'sky.provision.kubernetes.utils.get_all_kube_context_names',
            mock_get_context_names)

        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes/wrong-context')
        volume.validate(skip_cloud_compatibility=True)
        # Assert that kubernetes context validation was skipped.
        # This ensures that clients do not need to install
        # kubernetes as a dependency.
        mock_get_context_names.assert_not_called()

    def test_volume_validate_name_method(self):
        """Test Volume.validate_name method."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='100Gi')
        volume.validate_name()  # Should not raise

        # Test with None name
        volume.name = None
        with pytest.raises(AssertionError) as exc_info:
            volume.validate_name()
        assert 'Volume name must be set' in str(exc_info.value)

    def test_volume_validate_size_method(self):
        """Test Volume.validate_size method."""
        # Test with size
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='100Gi')
        volume.validate_size()  # Should not raise

        # Test with resource_name instead of size
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   resource_name='existing-pvc')
        volume.validate_size()  # Should not raise

        # Test with neither size nor resource_name
        volume = volume_lib.Volume(name='test', type='k8s-pvc')
        with pytest.raises(ValueError) as exc_info:
            volume.validate_size()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_validate_cloud_compatibility_method(self):
        """Test Volume.validate_cloud_compatibility method."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes')
        volume.validate_cloud_compatibility()  # Should not raise
        assert volume.cloud == 'kubernetes'

    def test_volume_normalize_config_method(self, monkeypatch):
        """Test Volume._normalize_config method."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   infra='kubernetes')

        assert volume.size == '100'
        assert volume.cloud == 'kubernetes'
        assert volume.region is None
        assert volume.zone is None

    def test_volume_normalize_config(self, monkeypatch):
        """Test Volume._normalize_config method."""
        volume = volume_lib.Volume(name='new-name',
                                   infra='k8s',
                                   type='k8s-pvc',
                                   size='200Gi')
        assert volume.name == 'new-name'
        assert volume.infra == 'k8s'
        assert volume.type == 'k8s-pvc'
        assert volume.size == '200'
        assert volume.cloud == 'kubernetes'
        assert volume.region is None
        assert volume.zone is None

    def test_volume_from_yaml_config(self):
        """Test Volume.from_yaml_config method."""
        config_dict = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'labels': {
                'key': 'value'
            },
            'size': '100Gi',
            'resource_name': 'existing-pvc',
            'config': {
                'access_mode': 'ReadWriteMany'
            }
        }

        volume = volume_lib.Volume.from_yaml_config(config_dict)

        assert volume.name == 'test-volume'
        assert volume.type == 'k8s-pvc'
        assert volume.infra == 'k8s'
        assert volume.labels == {'key': 'value'}
        assert volume.size == '100'
        assert volume.resource_name == 'existing-pvc'
        assert volume.config == {'access_mode': 'ReadWriteMany'}
        # Should be PVC subclass
        assert type(volume).__name__ in ('PVCVolume',)

    def test_volume_to_yaml_config(self):
        """Test Volume.to_yaml_config method."""
        volume = volume_lib.Volume(name='test-volume',
                                   type='k8s-pvc',
                                   infra='k8s',
                                   size='100Gi',
                                   resource_name='existing-pvc',
                                   labels={'key': 'value'},
                                   config={'access_mode': 'ReadWriteMany'})

        # Set cloud, region, zone
        volume.cloud = 'kubernetes'
        volume.region = 'us-west1'
        volume.zone = 'us-west1-a'

        result = volume.to_yaml_config()

        expected = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'labels': {
                'key': 'value'
            },
            'size': '100',
            'resource_name': 'existing-pvc',
            'config': {
                'access_mode': 'ReadWriteMany'
            },
            'cloud': 'kubernetes',
            'region': 'us-west1',
            'zone': 'us-west1-a'
        }

        assert result == expected

    def test_volume_schema_validation_valid_configs(self, monkeypatch):
        """Test volume schema validation with valid configurations."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        valid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'labels': {
                    'key': 'value'
                },
                'resource_name': 'existing-pvc'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi',
                'config': {
                    'access_mode': 'ReadWriteMany'
                }
            },
        ]

        for config in valid_configs:
            volume_lib.Volume.from_yaml_config(config)

    def test_volume_schema_validation_missing_required_fields(
            self, monkeypatch):
        """Test volume schema validation with missing required fields."""
        from sky import exceptions

        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        invalid_configs = [
            {
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi'
                # Missing name
            },
            {
                'name': 'test-volume',
                'infra': 'k8s',
                'size': '100Gi'
                # Missing type
            },
        ]

        # Missing name (valid type) -> schema validation error during normalize
        with pytest.raises(exceptions.InvalidSkyPilotConfigError) as exc_info:
            volume = volume_lib.Volume.from_yaml_config(invalid_configs[0])
            volume.validate()
        assert 'Invalid volumes config' in str(exc_info.value)
        # Missing type -> factory should raise immediately
        with pytest.raises(ValueError) as exc_info:
            volume = volume_lib.Volume.from_yaml_config(invalid_configs[1])
            volume.validate()
        assert 'Invalid volume type' in str(exc_info.value)

    def test_volume_schema_validation_invalid_type(self, monkeypatch):
        """Test volume schema validation with invalid type."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'invalid-type',
                'infra': 'k8s',
                'size': '100Gi'
            },
            {
                'name': 'test-volume',
                'type': 'pvc',  # Should be k8s-pvc
                'infra': 'k8s',
                'size': '100Gi'
            },
        ]

        for config in invalid_configs:
            with pytest.raises(ValueError) as exc_info:
                _ = volume_lib.Volume.from_yaml_config(config)
        assert 'Invalid volume type' in str(exc_info.value)

    def test_volume_schema_validation_invalid_size_pattern(self, monkeypatch):
        """Test volume schema validation with invalid size pattern."""
        from sky import exceptions

        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': 'invalid-size'
            },
        ]

        for config in invalid_configs:
            with pytest.raises(
                    exceptions.InvalidSkyPilotConfigError) as exc_info:
                volume_lib.Volume.from_yaml_config(config)
        assert 'Invalid volumes config: \'invalid-size\' does not match' in str(
            exc_info.value)

    def test_volume_schema_validation_invalid_config_object(self, monkeypatch):
        """Test volume schema validation with invalid config object."""
        from sky import exceptions

        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi',
                'config': 'not-a-dict'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi',
                'config': {
                    'access_mode': 'InvalidMode'
                }
            },
        ]

        for config in invalid_configs:
            with pytest.raises(exceptions.InvalidSkyPilotConfigError):
                volume_lib.Volume.from_yaml_config(config)

    def test_volume_schema_validation_additional_properties(self, monkeypatch):
        """Test volume schema validation with additional properties."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        config_with_extra = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'size': '100Gi',
            'extra_field': 'should-not-be-allowed'
        }

        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            common_utils.validate_schema(config_with_extra,
                                         schemas.get_volume_schema(),
                                         'Invalid volumes config: ')

    def test_volume_schema_validation_case_insensitive_enums(self, monkeypatch):
        """Test volume schema validation with case insensitive enums."""
        from sky import exceptions

        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'K8S-PVC',  # Wrong case
                'infra': 'k8s',
                'size': '100Gi'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi',
                'config': {
                    'access_mode': 'readwritemany'  # Wrong case
                }
            },
        ]

        # Case 1: wrong-cased type should fail at factory
        with pytest.raises(ValueError) as exc_info:
            volume = volume_lib.Volume.from_yaml_config(invalid_configs[0])
            volume.validate()
        assert 'Invalid volume type' in str(exc_info.value)
        # Case 2: wrong-cased access_mode should fail during normalize
        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            volume = volume_lib.Volume.from_yaml_config(invalid_configs[1])
            volume.validate()

    def test_volume_schema_validation_access_modes(self, monkeypatch):
        """Test volume schema validation with different access modes."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        valid_access_modes = [
            'ReadWriteOnce', 'ReadWriteOncePod', 'ReadWriteMany', 'ReadOnlyMany'
        ]

        for access_mode in valid_access_modes:
            config = {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': '100Gi',
                'config': {
                    'access_mode': access_mode
                }
            }
            volume_lib.Volume.from_yaml_config(config)

    def test_validate_with_valid_labels(self, monkeypatch):
        """Test Volume.validate with valid labels."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume_lib.Volume(name='test',
                          type='k8s-pvc',
                          infra=None,
                          size='100Gi',
                          labels={
                              'app': 'myapp',
                              'environment': 'production',
                              'app.kubernetes.io/name': 'myapp',
                              'app.kubernetes.io/version': 'v1.0.0'
                          })

    def test_validate_with_invalid_cloud(self):
        """Test Volume.validate with invalid cloud."""
        volume = volume_lib.Volume(
            name='test',
            type='k8s-pvc',
            infra='runpod',
            size='100Gi',
        )

        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'Invalid cloud' in str(exc_info.value)

    def test_validate_with_invalid_label_key(self, monkeypatch):
        """Test Volume.validate with invalid label key."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume = volume_lib.Volume(
            name='test',
            type='k8s-pvc',
            infra='k8s',
            size='100Gi',
            labels={
                'app': 'myapp',
                'invalid-key-': 'value'  # Invalid key (ends with dash)
            })
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'Invalid label key' in str(exc_info.value)

    def test_validate_with_invalid_label_value(self, monkeypatch):
        """Test Volume.validate with invalid label value."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume = volume_lib.Volume(
            name='test',
            type='k8s-pvc',
            infra='k8s',
            size='100Gi',
            labels={
                'app': 'myapp',
                'environment': 'invalid-value-'  # Invalid value (ends with dash)
            })
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'Invalid label value' in str(exc_info.value)

    def test_validate_with_empty_labels(self):
        """Test Volume.validate with empty labels."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   infra='k8s',
                                   size='100Gi',
                                   labels={})
        volume.validate()

    def test_validate_with_none_labels(self, monkeypatch):
        """Test Volume.validate with None labels."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   infra='k8s',
                                   size='100Gi',
                                   labels=None)
        volume.validate()

    def test_runpod_volume_validate_success(self, monkeypatch):
        """RunPod volume requires zone and min size; success case."""
        # Mock InfraInfo to resolve to runpod with a zone
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'runpod'
        mock_infra_info.region = None
        mock_infra_info.zone = 'iad-1'
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)
        # Bypass provider-specific zone validation
        monkeypatch.setattr('sky.clouds.runpod.RunPod.validate_region_zone',
                            lambda self, r, z: (r, z),
                            raising=True)

        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': '100'  # in GB
        }
        vol = volume_lib.Volume.from_yaml_config(cfg)
        vol.validate()
        # Should be subclass and not raise
        assert type(vol).__name__ in ('RunpodNetworkVolume',)

    def test_runpod_volume_missing_zone_raises(self, monkeypatch):
        """RunPod volume must have zone (DataCenterId) set."""
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'runpod'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)
        # Bypass provider-specific zone validation (not providing zone)
        monkeypatch.setattr('sky.clouds.runpod.RunPod.validate_region_zone',
                            lambda self, r, z: (r, z),
                            raising=True)

        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod',
            'size': '100'
        }
        volume = volume_lib.Volume.from_yaml_config(cfg)
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'RunPod DataCenterId is required to create a network volume' in str(
            exc_info.value)

    def test_runpod_volume_min_size_enforced(self, monkeypatch):
        from sky.utils import volume as utils_volume
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'runpod'
        mock_infra_info.region = None
        mock_infra_info.zone = 'iad-1'
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)
        monkeypatch.setattr('sky.clouds.runpod.RunPod.validate_region_zone',
                            lambda self, r, z: (r, z),
                            raising=True)

        min_size = utils_volume.MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB
        cfg = {
            'name': 'rpv',
            'type': 'runpod-network-volume',
            'infra': 'runpod/iad-1',
            'size': str(max(1, min_size - 1))
        }
        volume = volume_lib.Volume.from_yaml_config(cfg)
        with pytest.raises(ValueError) as exc_info:
            volume.validate()
        assert 'RunPod network volume size must be at least' in str(
            exc_info.value)


class TestVolumeConfigModel:

    def test_pickle_unpickle(self):
        config = models.VolumeConfig(name='test',
                                     type='k8s-pvc',
                                     size='100Gi',
                                     cloud='kubernetes',
                                     region=None,
                                     zone=None,
                                     name_on_cloud='test-pvc')
        pickled_config = pickle.dumps(config)
        unpickled_config = pickle.loads(pickled_config)
        assert unpickled_config.name == 'test'
        assert unpickled_config.type == 'k8s-pvc'
        assert unpickled_config.size == '100Gi'
        assert unpickled_config.cloud == 'kubernetes'
        assert unpickled_config.region is None
        assert unpickled_config.zone is None
        assert unpickled_config.name_on_cloud == 'test-pvc'
        assert unpickled_config.id_on_cloud is None
        assert unpickled_config._version == models.VolumeConfig._VERSION
