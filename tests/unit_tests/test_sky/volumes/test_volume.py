"""Tests for volume class."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky.utils import common_utils
from sky.utils import schemas
from sky.volumes import volume as volume_lib


class TestVolume:

    def test_volume_adjust_config_valid_sizes(self):
        """Test Volume._adjust_config with valid sizes."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

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
        volume = volume_lib.Volume(name='test', type='k8s-pvc')
        volume._adjust_config()
        assert volume.size is None

    def test_volume_adjust_config_invalid_size(self):
        """Test Volume._adjust_config with invalid size."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Test various valid size formats
        test_cases = ['50Mi', 'invalid', '0']

        for input_size in test_cases:
            volume.size = input_size
            with pytest.raises(ValueError) as exc_info:
                volume._adjust_config()
            assert 'Invalid size' in str(exc_info.value)

    def test_volume_adjust_config_edge_cases(self):
        """Test Volume._adjust_config with edge cases."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Test with None size
        volume.size = None
        volume._adjust_config()
        assert volume.size is None

        # Test with empty string
        volume.size = ''
        with pytest.raises(ValueError):
            volume._adjust_config()

    def test_volume_validate_config_valid_with_size(self):
        """Test Volume._validate_config with valid size."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='100Gi')
        volume._validate_config()  # Should not raise

    def test_volume_validate_config_valid_with_resource_name(self):
        """Test Volume._validate_config with valid resource_name."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   resource_name='existing-pvc')
        volume._validate_config()  # Should not raise

    def test_volume_validate_config_valid_with_both(self):
        """Test Volume._validate_config with both size and resource_name."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   resource_name='existing-pvc')
        volume._validate_config()  # Should not raise

    def test_volume_validate_config_missing_size_and_resource_name(self):
        """Test Volume._validate_config with missing size and resource_name."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_validate_config_empty_config(self):
        """Test Volume._validate_config with empty config."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', config={})

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_validate_config_none_values(self):
        """Test Volume._validate_config with None values."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')
        volume.size = None
        volume.resource_name = None

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_validate_config_empty_strings(self):
        """Test Volume._validate_config with empty strings."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')
        volume.size = ''
        volume.resource_name = ''

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Size is required for new volumes' in str(exc_info.value)

    def test_volume_adjust_and_validate_config_integration(self):
        """Test integration of adjust and validate config."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='100Gi')

        # Should work together
        volume._adjust_config()
        volume._validate_config()

        assert volume.size == '100'  # Size should remain unchanged

    def test_volume_normalize_config(self, monkeypatch):
        """Test Volume.normalize_config method."""
        # Mock infra_utils.InfraInfo.from_str
        mock_infra_info = MagicMock()
        mock_infra_info.cloud = 'kubernetes'
        mock_infra_info.region = None
        mock_infra_info.zone = None
        monkeypatch.setattr('sky.utils.infra_utils.InfraInfo.from_str',
                            lambda x: mock_infra_info)

        volume = volume_lib.Volume(name='test', type='k8s-pvc', size='100Gi')

        # Test normalize_config with CLI overrides
        volume.normalize_config(name='new-name',
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

    def test_volume_from_dict(self):
        """Test Volume.from_dict method."""
        config_dict = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'size': '100Gi',
            'resource_name': 'existing-pvc',
            'config': {
                'access_mode': 'ReadWriteMany'
            }
        }

        volume = volume_lib.Volume.from_dict(config_dict)

        assert volume.name == 'test-volume'
        assert volume.type == 'k8s-pvc'
        assert volume.infra == 'k8s'
        assert volume.size == '100Gi'
        assert volume.resource_name == 'existing-pvc'
        assert volume.config == {'access_mode': 'ReadWriteMany'}

    def test_volume_to_dict(self):
        """Test Volume.to_dict method."""
        volume = volume_lib.Volume(name='test-volume',
                                   type='k8s-pvc',
                                   infra='k8s',
                                   size='100Gi',
                                   resource_name='existing-pvc',
                                   config={'access_mode': 'ReadWriteMany'})

        # Set cloud, region, zone
        volume.cloud = 'kubernetes'
        volume.region = 'us-west1'
        volume.zone = 'us-west1-a'

        result = volume.to_dict()

        expected = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'size': '100Gi',
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
            volume = volume_lib.Volume.from_dict(config)
            volume.normalize_config()  # Should not raise

    def test_volume_schema_validation_missing_required_fields(
            self, monkeypatch):
        """Test volume schema validation with missing required fields."""
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
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'size': '100Gi'
                # Missing infra
            },
        ]

        for config in invalid_configs:
            volume = volume_lib.Volume.from_dict(config)
            with pytest.raises(exceptions.InvalidSkyPilotConfigError):
                volume.normalize_config()

    def test_volume_schema_validation_invalid_type(self, monkeypatch):
        """Test volume schema validation with invalid type."""
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
            volume = volume_lib.Volume.from_dict(config)
            with pytest.raises(exceptions.InvalidSkyPilotConfigError):
                volume.normalize_config()

    def test_volume_schema_validation_invalid_size_pattern(self, monkeypatch):
        """Test volume schema validation with invalid size pattern."""
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

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'size': 'invalid-size'
            },
        ]

        for config in invalid_configs:
            volume = volume_lib.Volume.from_dict(config)
            with pytest.raises(ValueError):
                volume.normalize_config()

    def test_volume_schema_validation_invalid_config_object(self, monkeypatch):
        """Test volume schema validation with invalid config object."""
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
            volume = volume_lib.Volume.from_dict(config)
            with pytest.raises(exceptions.InvalidSkyPilotConfigError):
                volume.normalize_config()

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
        from sky.utils import common_utils
        from sky.utils import schemas

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

        for config in invalid_configs:
            volume = volume_lib.Volume.from_dict(config)
            with pytest.raises(exceptions.InvalidSkyPilotConfigError):
                volume.normalize_config()

    def test_volume_schema_validation_access_modes(self, monkeypatch):
        """Test volume schema validation with different access modes."""
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
            volume = volume_lib.Volume.from_dict(config)
            volume.normalize_config()  # Should not raise
