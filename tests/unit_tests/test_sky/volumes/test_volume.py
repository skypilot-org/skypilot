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
            'labels': {
                'key': 'value'
            },
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
        assert volume.labels == {'key': 'value'}
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
                                   labels={'key': 'value'},
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
            'labels': {
                'key': 'value'
            },
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

    def test_is_valid_label_key_valid_cases(self):
        """Test Volume.is_valid_label_key with valid label keys."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Valid label keys for Kubernetes
        valid_keys = [
            'app',
            'app.kubernetes.io/name',
            'kubernetes.io/name',
            'my-label',
            'my_label',
            'my.label',
            'my-label-123',
            'my-label_123',
            'my-label.123',
            'ab',  # Two characters (must start and end with alphanumeric)
            'a' * 63,  # Maximum length
            'my-prefix/my-label',  # With prefix
            'my-prefix/my-label-123',
        ]

        for key in valid_keys:
            assert volume.is_valid_label_key(
                key), f"Label key '{key}' should be valid"

    def test_is_valid_label_key_invalid_cases(self):
        """Test Volume.is_valid_label_key with invalid label keys."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Invalid label keys for Kubernetes
        invalid_keys = [
            '',  # Empty string
            '-my-label',  # Starts with dash
            'my-label-',  # Ends with dash
            'my-label--',  # Double dash
            'my-label_',  # Ends with underscore
            '_my-label',  # Starts with underscore
            'my-label.',  # Ends with dot
            '.my-label',  # Starts with dot
            'my-label..',  # Double dot
            'my-label/',  # Ends with slash
            '/my-label',  # Starts with slash
            'my-prefix//my-label',  # Double slash
            'my-prefix/my-label/',  # Ends with slash after prefix
            '/my-prefix/my-label',  # Starts with slash before prefix
            'a' * 64,  # Too long
            'my-prefix/' + 'a' * 64,  # Name too long with prefix
            'a' * 254 + '/my-label',  # Prefix too long
            'my-label@',  # Invalid character
            'my-label/',  # Invalid character
            'my-label ',  # Space
            ' my-label',  # Leading space
            'my-label ',  # Trailing space
        ]

        for key in invalid_keys:
            assert not volume.is_valid_label_key(
                key), f"Label key '{key}' should be invalid"

    def test_is_valid_label_key_non_pvc_type(self):
        """Test Volume.is_valid_label_key with non-PVC volume types."""
        # Test with different volume types
        volume_types = ['aws-ebs', 'gcp-disk', 'azure-disk', None]

        for vol_type in volume_types:
            volume = volume_lib.Volume(name='test', type=vol_type)
            # For non-PVC types, should return False
            assert not volume.is_valid_label_key(
                'app'), f"Non-PVC type '{vol_type}' should not support labels"

    def test_is_valid_label_value_valid_cases(self):
        """Test Volume.is_valid_label_value with valid label values."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Valid label values for Kubernetes
        valid_values = [
            'app',
            'my-value',
            'my_value',
            'my.value',
            'my-value-123',
            'my-value_123',
            'my-value.123',
            'ab',  # Two characters (must start and end with alphanumeric)
            'a' * 63,  # Maximum length
            '',  # Empty string is valid for Kubernetes labels
            '123',
            'app-123',
            'app_123',
            'app.123',
        ]

        for value in valid_values:
            assert volume.is_valid_label_value(
                value), f"Label value '{value}' should be valid"

    def test_is_valid_label_value_invalid_cases(self):
        """Test Volume.is_valid_label_value with invalid label values."""
        volume = volume_lib.Volume(name='test', type='k8s-pvc')

        # Invalid label values for Kubernetes
        invalid_values = [
            '-my-value',  # Starts with dash
            'my-value-',  # Ends with dash
            'my-value--',  # Double dash
            'my-value_',  # Ends with underscore
            '_my-value',  # Starts with underscore
            'my-value.',  # Ends with dot
            '.my-value',  # Starts with dot
            'my-value..',  # Double dot
            'a' * 64,  # Too long
            'my-value@',  # Invalid character
            'my-value/',  # Invalid character
            'my-value ',  # Space
            ' my-value',  # Leading space
            'my-value ',  # Trailing space
        ]

        for value in invalid_values:
            assert not volume.is_valid_label_value(
                value), f"Label value '{value}' should be invalid"

    def test_is_valid_label_value_non_pvc_type(self):
        """Test Volume.is_valid_label_value with non-PVC volume types."""
        # Test with different volume types
        volume_types = ['aws-ebs', 'gcp-disk', 'azure-disk', None]

        for vol_type in volume_types:
            volume = volume_lib.Volume(name='test', type=vol_type)
            # For non-PVC types, should return False
            assert not volume.is_valid_label_value(
                'app'), f"Non-PVC type '{vol_type}' should not support labels"

    def test_validate_config_with_valid_labels(self):
        """Test Volume._validate_config with valid labels."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   labels={
                                       'app': 'myapp',
                                       'environment': 'production',
                                       'app.kubernetes.io/name': 'myapp',
                                       'app.kubernetes.io/version': 'v1.0.0'
                                   })

        # Should not raise any exception
        volume._validate_config()

    def test_validate_config_with_invalid_label_key(self):
        """Test Volume._validate_config with invalid label key."""
        volume = volume_lib.Volume(
            name='test',
            type='k8s-pvc',
            size='100Gi',
            labels={
                'app': 'myapp',
                'invalid-key-': 'value'  # Invalid key (ends with dash)
            })

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Invalid label key' in str(exc_info.value)

    def test_validate_config_with_invalid_label_value(self):
        """Test Volume._validate_config with invalid label value."""
        volume = volume_lib.Volume(
            name='test',
            type='k8s-pvc',
            size='100Gi',
            labels={
                'app': 'myapp',
                'environment': 'invalid-value-'  # Invalid value (ends with dash)
            })

        with pytest.raises(ValueError) as exc_info:
            volume._validate_config()
        assert 'Invalid label value' in str(exc_info.value)

    def test_validate_config_with_empty_labels(self):
        """Test Volume._validate_config with empty labels."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   labels={})

        # Should not raise any exception
        volume._validate_config()

    def test_validate_config_with_none_labels(self):
        """Test Volume._validate_config with None labels."""
        volume = volume_lib.Volume(name='test',
                                   type='k8s-pvc',
                                   size='100Gi',
                                   labels=None)

        # Should not raise any exception
        volume._validate_config()
