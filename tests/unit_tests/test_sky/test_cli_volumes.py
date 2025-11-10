"""Unit tests for CLI volumes commands."""
from datetime import datetime
from unittest import mock

from click import testing as cli_testing
import pytest

from sky.client.cli import command
from sky.client.cli import table_utils
from sky.utils import volume as volume_utils


class TestVolumeCommands:

    def test_volumes_apply_with_yaml(self, monkeypatch):
        """Test `sky volumes apply` with YAML file."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function
        mock_yaml_config = {
            'name': 'test-volume',
            'infra': 'k8s',
            'type': 'k8s-pvc',
            'size': '100Gi'
        }
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (True, mock_yaml_config, True, ''))

        # Mock the volumes SDK
        mock_apply = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.apply', mock_apply)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test with YAML file
        result = cli_runner.invoke(command.volumes_apply, ['volume.yaml'])
        assert not result.exit_code
        mock_apply.assert_called_once()
        mock_async_call.assert_called_once_with('request-id', False,
                                                'sky.volumes.apply')

    def test_volumes_apply_without_yaml(self, monkeypatch):
        """Test `sky volumes apply` without YAML file."""
        cli_runner = cli_testing.CliRunner()
        # Mock the YAML check function to return no YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, False, ''))
        # Test with no arguments
        result = cli_runner.invoke(command.volumes_apply, ['ab'])
        assert result.exit_code != 0
        assert 'needs to be a YAML file' in result.output

    def test_volumes_apply_with_cli_options(self, monkeypatch):
        """Test `sky volumes apply` with CLI options."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function to return no YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, False, ''))

        # Mock the volumes SDK
        mock_apply = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.apply', mock_apply)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Mock schema validation and other utilities
        monkeypatch.setattr('sky.utils.common_utils.validate_schema',
                            lambda *args, **kwargs: None)

        # Test with CLI options
        result = cli_runner.invoke(command.volumes_apply, [
            '--name', 'test-volume', '--infra', 'k8s', '--type', 'k8s-pvc',
            '--size', '100Gi'
        ])
        assert not result.exit_code
        mock_apply.assert_called_once()
        mock_async_call.assert_called_once_with('request-id', False,
                                                'sky.volumes.apply')

    def test_volumes_apply_invalid_yaml(self, monkeypatch):
        """Test `sky volumes apply` with invalid YAML."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function to return invalid YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, True, 'invalid format'))

        # Test with invalid YAML file
        result = cli_runner.invoke(command.volumes_apply, ['invalid.yaml'])
        assert result.exit_code != 0
        # Check for the error message in the output instead of exception
        assert 'looks like a yaml path but invalid format' in result.output

    def test_volumes_apply_invalid_type_cli(self, monkeypatch):
        """Test `sky volumes apply` with invalid type via CLI."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function to return no YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, False, ''))

        # Test with invalid type value
        result = cli_runner.invoke(command.volumes_apply, [
            '--name', 'test-volume', '--infra', 'k8s', '--type', 'pvc',
            '--size', '100Gi'
        ])
        assert result.exit_code != 0
        # Check that click.Choice rejected the invalid value
        types_str = ', '.join(
            f"'{t}'" for t in volume_utils.VolumeType.supported_types())
        assert f"Error: Invalid value for '--type': 'pvc' is not one of {types_str}." in result.output

    def test_volumes_apply_no_yaml_or_options(self, monkeypatch):
        """Test `sky volumes apply` with no YAML or options."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function to return no YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, False, ''))

        # Mock schema validation to raise error for empty config
        def mock_validate_schema(config, schema, error_prefix):
            if not config:
                raise ValueError('Empty config')

        monkeypatch.setattr('sky.utils.common_utils.validate_schema',
                            mock_validate_schema)

        # Test with no arguments
        result = cli_runner.invoke(command.volumes_apply, [])
        assert result.exit_code != 0

    def test_volumes_ls(self, monkeypatch):
        """Test `sky volumes ls` command."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [
            {
                'name': 'volume1',
                'infra': 'k8s',
                'type': 'k8s-pvc',
                'size': '100Gi',
                'status': 'READY',
                'created_at': '2024-01-01T00:00:00Z',
                'last_attached_at': None
            },
            {
                'name': 'volume2',
                'infra': 'k8s',
                'type': 'k8s-pvc',
                'size': '200Gi',
                'status': 'READY',
                'created_at': '2024-01-02T00:00:00Z',
                'last_attached_at': 1704067200  # 2024-01-01 12:00:00
            }
        ]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)

        # Mock the SDK stream_and_get
        mock_stream_and_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)

        # Mock the volume table formatting
        mock_table = "Volume Table Output"
        monkeypatch.setattr('sky.client.cli.table_utils.format_volume_table',
                            lambda *args, **kwargs: mock_table)

        # Test basic ls command
        result = cli_runner.invoke(command.volumes_ls, [])
        assert not result.exit_code
        assert mock_table in result.output
        mock_ls.assert_called_once()
        mock_stream_and_get.assert_called_once_with('request-id')

    def test_volumes_ls_verbose(self, monkeypatch):
        """Test `sky volumes ls` command with verbose flag."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{
            'name': 'volume1',
            'infra': 'k8s',
            'type': 'k8s-pvc',
            'size': '100Gi',
            'status': 'READY',
            'created_at': '2024-01-01T00:00:00Z',
            'last_attached_at': None
        }]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)

        # Mock the SDK stream_and_get
        mock_stream_and_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.stream_and_get',
                            mock_stream_and_get)

        # Mock the volume table formatting
        mock_table = "Verbose Volume Table Output"
        mock_format_table = mock.MagicMock(return_value=mock_table)
        monkeypatch.setattr('sky.client.cli.table_utils.format_volume_table',
                            mock_format_table)

        # Test verbose ls command
        result = cli_runner.invoke(command.volumes_ls, ['--verbose'])
        assert not result.exit_code
        assert mock_table in result.output
        mock_format_table.assert_called_once_with(mock_volumes, show_all=True)

    def test_volumes_delete_specific_volumes(self, monkeypatch):
        """Test `sky volumes delete` with specific volume names."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{
            'name': 'volume1'
        }, {
            'name': 'volume2'
        }, {
            'name': 'volume3'
        }]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        mock_delete = mock.MagicMock(return_value='delete-request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)
        monkeypatch.setattr('sky.volumes.client.sdk.delete', mock_delete)

        # Mock the SDK get and stream_and_get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test deleting specific volumes
        result = cli_runner.invoke(command.volumes_delete,
                                   ['volume1', 'volume2'])
        assert not result.exit_code
        # Check that delete was called with the correct arguments (order may vary)
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args[0][0]
        assert set(call_args) == {'volume1', 'volume2'}
        mock_async_call.assert_called_once_with('delete-request-id', False,
                                                'sky.volumes.delete')

    def test_volumes_delete_all_volumes(self, monkeypatch):
        """Test `sky volumes delete` with --all flag."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{'name': 'volume1'}, {'name': 'volume2'}]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        mock_delete = mock.MagicMock(return_value='delete-request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)
        monkeypatch.setattr('sky.volumes.client.sdk.delete', mock_delete)

        # Mock the SDK get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test deleting all volumes
        result = cli_runner.invoke(command.volumes_delete, ['--all'])
        assert not result.exit_code
        mock_delete.assert_called_once_with(['volume1', 'volume2'])
        mock_async_call.assert_called_once_with('delete-request-id', False,
                                                'sky.volumes.delete')

    def test_volumes_delete_no_volumes(self, monkeypatch):
        """Test `sky volumes delete` when no volumes exist."""
        cli_runner = cli_testing.CliRunner()

        # Mock empty volumes data
        mock_volumes = []

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)

        # Mock the SDK get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Test deleting all volumes when none exist
        result = cli_runner.invoke(command.volumes_delete, ['--all'])
        assert not result.exit_code
        assert 'No volumes to delete.' in result.output

    def test_volumes_delete_no_matches(self, monkeypatch):
        """Test `sky volumes delete` when no volumes match the pattern."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{'name': 'volume1'}, {'name': 'volume2'}]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)

        # Mock the SDK get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Test deleting non-existent volumes
        result = cli_runner.invoke(command.volumes_delete, ['nonexistent'])
        assert not result.exit_code

    def test_volumes_delete_invalid_arguments(self, monkeypatch):
        """Test `sky volumes delete` with invalid arguments."""
        cli_runner = cli_testing.CliRunner()

        # Test with both --all and specific names (should fail)
        result = cli_runner.invoke(command.volumes_delete, ['--all', 'volume1'])
        assert result.exit_code != 0
        assert 'Either --all or a name must be specified' in result.output

        # Test with neither --all nor names (should fail)
        result = cli_runner.invoke(command.volumes_delete, [])
        assert result.exit_code != 0
        assert 'Either --all or a name must be specified' in result.output

    def test_volumes_delete_with_glob_pattern(self, monkeypatch):
        """Test `sky volumes delete` with glob patterns."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{
            'name': 'volume1'
        }, {
            'name': 'volume2'
        }, {
            'name': 'data1'
        }, {
            'name': 'data2'
        }]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        mock_delete = mock.MagicMock(return_value='delete-request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)
        monkeypatch.setattr('sky.volumes.client.sdk.delete', mock_delete)

        # Mock the SDK get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test deleting volumes with glob pattern
        result = cli_runner.invoke(command.volumes_delete, ['volume*'])
        assert not result.exit_code
        # Check that delete was called with the correct arguments (order may vary)
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args[0][0]
        assert set(call_args) == {'volume1', 'volume2'}
        mock_async_call.assert_called_once_with('delete-request-id', False,
                                                'sky.volumes.delete')

    def test_volume_schema_validation_valid_configs(self, monkeypatch):
        """Test volume schema validation with valid configurations."""
        from sky.utils import common_utils
        from sky.utils import schemas

        # Mock the schema validation to avoid actual validation
        mock_validate = mock.MagicMock()
        monkeypatch.setattr('sky.utils.common_utils.validate_schema',
                            mock_validate)

        # Test various valid volume configurations
        valid_configs = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'size': '100Gi'
        }, {
            'name': 'test-volume-2',
            'type': 'k8s-pvc',
            'infra': 'k8s/my-context',
            'size': '200Gi',
            'resource_name': 'existing-pvc'
        }, {
            'name': 'test-volume-3',
            'type': 'k8s-pvc',
            'infra': 'kubernetes',
            'size': '500Gi',
            'config': {
                'storage_class_name': 'gp2',
                'access_mode': 'ReadWriteOnce',
                'namespace': 'default'
            }
        }, {
            'name': 'test-volume-4',
            'type': 'k8s-pvc',
            'infra': 'kubernetes/context-name',
            'size': '1Ti'
        }, {
            'name': 'test-volume-5',
            'type': 'k8s-pvc',
            'infra': 'k8s/aws:eks:us-east-1:123456789012:cluster/my-cluster',
            'size': '100Gi'
        }]

        for config in valid_configs:
            # Reset mock for each test
            mock_validate.reset_mock()

            # Call the validation function
            common_utils.validate_schema(config, schemas.get_volume_schema(),
                                         'Invalid volumes config: ')

            # Verify validation was called with correct arguments
            mock_validate.assert_called_once_with(config,
                                                  schemas.get_volume_schema(),
                                                  'Invalid volumes config: ')

    def test_volume_schema_validation_missing_required_fields(
            self, monkeypatch):
        """Test volume schema validation with missing required fields."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        # Test missing required fields
        invalid_configs = [
            {},  # Missing all required fields
            {
                'name': 'test-volume'
            },  # Missing type and infra
            {
                'type': 'k8s-pvc'
            },  # Missing name and infra
            {
                'infra': 'k8s'
            },  # Missing name and type
            {
                'name': 'test-volume',
                'infra': 'k8s'
            },  # Missing type
            {
                'type': 'k8s-pvc',
                'infra': 'k8s'
            },  # Missing name
        ]

        for config in invalid_configs:
            with pytest.raises(
                    exceptions.InvalidSkyPilotConfigError) as exc_info:
                common_utils.validate_schema(config,
                                             schemas.get_volume_schema(),
                                             'Invalid volumes config: ')
            # Should raise some kind of validation error
            assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_invalid_type(self, monkeypatch):
        """Test volume schema validation with invalid volume type."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'invalid-type',
                'infra': 'k8s'
            },
            {
                'name': 'test-volume',
                'type': 'pvc',  # Should be 'k8s-pvc'
                'infra': 'k8s'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-PVC',  # Should be 'k8s-pvc'
                'infra': 'k8s'
            },
        ]

        for config in invalid_configs:
            with pytest.raises(
                    exceptions.InvalidSkyPilotConfigError) as exc_info:
                common_utils.validate_schema(config,
                                             schemas.get_volume_schema(),
                                             'Invalid volumes config: ')
            assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_invalid_infra(self, monkeypatch):
        """Test volume schema validation with invalid infra patterns."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'invalid-cloud'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'aws/invalid-region/invalid-zone/invalid-subzone'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'aws//us-east-1a'  # Empty region
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'aws/us-east-1/'  # Empty zone
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': '*',
                'size': '100Gi'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': '*/us-east-1',
                'size': '100Gi'
            },
        ]

        for config in invalid_configs:
            with pytest.raises(
                    exceptions.InvalidSkyPilotConfigError) as exc_info:
                common_utils.validate_schema(config,
                                             schemas.get_volume_schema(),
                                             'Invalid volumes config: ')
            assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_invalid_size_pattern(self, monkeypatch):
        """Test volume schema validation with invalid size patterns."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

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
                common_utils.validate_schema(config,
                                             schemas.get_volume_schema(),
                                             'Invalid volumes config: ')
            assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_invalid_config_object(self, monkeypatch):
        """Test volume schema validation with invalid config object."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        invalid_configs = [
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'config': 'not-an-object'
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'config': {
                    'access_mode': 'InvalidMode'
                }
            },
            {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'config': {
                    'access_mode': 'ReadWriteonce'
                }
            },
        ]

        for config in invalid_configs:
            with pytest.raises(
                    exceptions.InvalidSkyPilotConfigError) as exc_info:
                common_utils.validate_schema(config,
                                             schemas.get_volume_schema(),
                                             'Invalid volumes config: ')
            assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_additional_properties(self, monkeypatch):
        """Test volume schema validation with additional properties."""
        from sky import exceptions
        from sky.utils import common_utils
        from sky.utils import schemas

        config_with_extra = {
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s',
            'size': '100Gi',
            'extra_field': 'should-not-be-allowed'
        }

        with pytest.raises(exceptions.InvalidSkyPilotConfigError) as exc_info:
            common_utils.validate_schema(config_with_extra,
                                         schemas.get_volume_schema(),
                                         'Invalid volumes config: ')
        assert 'Invalid volumes config' in str(exc_info.value)

    def test_volume_schema_validation_case_insensitive_enums(self, monkeypatch):
        """Test volume schema validation with case insensitive enums."""
        from sky.utils import common_utils
        from sky.utils import schemas

        # Test case insensitive volume types
        valid_case_variants = [{
            'name': 'test-volume',
            'type': 'k8s-pvc',
            'infra': 'k8s'
        }, {
            'name': 'test-volume',
            'type': 'K8S-PVC',
            'infra': 'k8s'
        }, {
            'name': 'test-volume',
            'type': 'K8s-Pvc',
            'infra': 'k8s'
        }]

        # Mock the schema validation to avoid actual validation
        mock_validate = mock.MagicMock()
        monkeypatch.setattr('sky.utils.common_utils.validate_schema',
                            mock_validate)

        for config in valid_case_variants:
            mock_validate.reset_mock()
            common_utils.validate_schema(config, schemas.get_volume_schema(),
                                         'Invalid volumes config: ')
            mock_validate.assert_called_once()

    def test_volume_schema_validation_access_modes(self, monkeypatch):
        """Test volume schema validation with different access modes."""
        from sky.utils import common_utils
        from sky.utils import schemas

        # Mock the schema validation to avoid actual validation
        mock_validate = mock.MagicMock()
        monkeypatch.setattr('sky.utils.common_utils.validate_schema',
                            mock_validate)

        valid_access_modes = [
            'ReadWriteOnce', 'ReadWriteOncePod', 'ReadWriteMany', 'ReadOnlyMany'
        ]

        for access_mode in valid_access_modes:
            config = {
                'name': 'test-volume',
                'type': 'k8s-pvc',
                'infra': 'k8s',
                'config': {
                    'access_mode': access_mode
                }
            }

            mock_validate.reset_mock()
            common_utils.validate_schema(config, schemas.get_volume_schema(),
                                         'Invalid volumes config: ')
            mock_validate.assert_called_once()

    def test_volumes_apply_api_not_supported_fallback(self, monkeypatch):
        """Test `sky volumes apply` with APINotSupportedError fallback (lines 4364-4366)."""
        from sky import exceptions
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function
        mock_yaml_config = {
            'name': 'test-volume',
            'infra': 'k8s',
            'type': 'k8s-pvc',
            'size': '100Gi'
        }
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (True, mock_yaml_config, True, ''))

        # Mock Volume.from_yaml_config
        mock_volume = mock.MagicMock()
        mock_volume.name = 'test-volume'
        mock_volume.to_yaml_config.return_value = mock_yaml_config
        mock_volume.validate = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.volume.Volume.from_yaml_config',
                            lambda config: mock_volume)

        # Mock volumes_sdk.validate to raise APINotSupportedError
        mock_validate = mock.MagicMock(
            side_effect=exceptions.APINotSupportedError('API not supported'))
        monkeypatch.setattr('sky.volumes.client.sdk.validate', mock_validate)

        # Mock volumes_sdk.apply
        mock_apply = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.apply', mock_apply)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test with YAML file
        result = cli_runner.invoke(command.volumes_apply, ['volume.yaml'])
        assert not result.exit_code

        # Verify that validate was called and client-side validation was used
        mock_validate.assert_called_once()
        mock_volume.validate.assert_called_once_with(
            skip_cloud_compatibility=True)

    def test_volumes_apply_runtime_error(self, monkeypatch):
        """Test `sky volumes apply` with RuntimeError during apply (lines 4378-4379)."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function
        mock_yaml_config = {
            'name': 'test-volume',
            'infra': 'k8s',
            'type': 'k8s-pvc',
            'size': '100Gi'
        }
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (True, mock_yaml_config, True, ''))

        # Mock Volume.from_yaml_config
        mock_volume = mock.MagicMock()
        mock_volume.name = 'test-volume'
        mock_volume.to_yaml_config.return_value = mock_yaml_config
        monkeypatch.setattr('sky.volumes.volume.Volume.from_yaml_config',
                            lambda config: mock_volume)

        # Mock volumes_sdk.validate
        mock_validate = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.client.sdk.validate', mock_validate)

        # Mock volumes_sdk.apply to raise RuntimeError
        mock_apply = mock.MagicMock(
            side_effect=RuntimeError('Failed to apply volume'))
        monkeypatch.setattr('sky.volumes.client.sdk.apply', mock_apply)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Test with YAML file
        result = cli_runner.invoke(command.volumes_apply, ['volume.yaml'])
        # The command should not crash but handle the error gracefully
        assert not result.exit_code
        mock_apply.assert_called_once()

    def test_volumes_apply_with_use_existing(self, monkeypatch):
        """Test `sky volumes apply` with --use-existing option (line 4402)."""
        cli_runner = cli_testing.CliRunner()

        # Mock the YAML check function to return no YAML
        monkeypatch.setattr('sky.client.cli.command._check_yaml_only', lambda x:
                            (False, None, False, ''))

        # Mock Volume.from_yaml_config to capture the config
        captured_config = {}

        def capture_config(config):
            captured_config.update(config)
            mock_volume = mock.MagicMock()
            mock_volume.name = 'test-volume'
            mock_volume.to_yaml_config.return_value = config
            return mock_volume

        monkeypatch.setattr('sky.volumes.volume.Volume.from_yaml_config',
                            capture_config)

        # Mock volumes_sdk
        mock_validate = mock.MagicMock()
        mock_apply = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.validate', mock_validate)
        monkeypatch.setattr('sky.volumes.client.sdk.apply', mock_apply)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock the async call function
        mock_async_call = mock.MagicMock()
        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_call)

        # Test with --use-existing flag
        result = cli_runner.invoke(command.volumes_apply, [
            '--name', 'test-volume', '--infra', 'k8s', '--type', 'k8s-pvc',
            '--size', '100Gi', '--use-existing'
        ])
        assert not result.exit_code
        # Verify that use_existing was included in the config
        assert 'use_existing' in captured_config
        assert captured_config['use_existing'] is True

        # Test with --no-use-existing flag
        captured_config.clear()
        result = cli_runner.invoke(command.volumes_apply, [
            '--name', 'test-volume', '--infra', 'k8s', '--type', 'k8s-pvc',
            '--size', '100Gi', '--no-use-existing'
        ])
        assert not result.exit_code
        # Verify that use_existing was included in the config
        assert 'use_existing' in captured_config
        assert captured_config['use_existing'] is False

    def test_volumes_delete_exception_handling(self, monkeypatch):
        """Test `sky volumes delete` with exception during deletion (lines 4488-4489)."""
        cli_runner = cli_testing.CliRunner()

        # Mock volumes data
        mock_volumes = [{'name': 'volume1'}, {'name': 'volume2'}]

        # Mock the volumes SDK
        mock_ls = mock.MagicMock(return_value='request-id')
        monkeypatch.setattr('sky.volumes.client.sdk.ls', mock_ls)

        # Mock the SDK get
        mock_get = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr('sky.client.sdk.get', mock_get)

        # Mock click.confirm to avoid interactive prompts
        monkeypatch.setattr('click.confirm', lambda *args, **kwargs: True)

        # Mock volumes_sdk.delete to raise an exception
        mock_delete = mock.MagicMock(side_effect=Exception('Network error'))
        monkeypatch.setattr('sky.volumes.client.sdk.delete', mock_delete)

        # Test deleting volumes with exception
        result = cli_runner.invoke(command.volumes_delete, ['volume1'])
        # The command should not crash but handle the error gracefully
        assert not result.exit_code
        mock_delete.assert_called_once()


class TestPVCVolumeTable:
    """Test cases for PVCVolumeTable."""

    def test_pvc_volume_table_init(self):
        """Test PVCVolumeTable initialization."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=False)
        assert table is not None
        assert hasattr(table, 'table')

    def test_pvc_volume_table_format_basic(self):
        """Test PVCVolumeTable formatting with basic columns."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result
        assert '100Gi' in result

    def test_pvc_volume_table_format_show_all(self):
        """Test PVCVolumeTable formatting with show_all=True."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default',
                'storage_class_name': 'gp2',
                'access_mode': 'ReadWriteOnce'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply',
            'name_on_cloud': 'test-volume-1-abc123'
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=True)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'test-volume-1-abc123' in result
        assert 'gp2' in result
        assert 'ReadWriteOnce' in result

    def test_pvc_volume_table_empty_volumes(self):
        """Test PVCVolumeTable with empty volumes list."""
        volumes = []

        table = table_utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        # For empty volumes, the table returns an empty string
        assert result == 'Kubernetes PVCs:\n'

    def test_pvc_volume_table_null_values(self):
        """Test PVCVolumeTable with null/None values."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': None,
            'config': {},
            'size': None,
            'user_hash': '',
            'workspace': None,
            'launched_at': None,
            'last_attached_at': None,
            'status': None,
            'last_use': ''  # Use empty string instead of None to avoid truncate error
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result

    def test_pvc_volume_table_timestamp_conversion(self):
        """Test PVCVolumeTable timestamp conversion."""
        test_timestamp = 1234567890
        expected_time = datetime.fromtimestamp(test_timestamp).strftime(
            '%Y-%m-%d %H:%M:%S')

        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': test_timestamp,
            'last_attached_at': test_timestamp,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert expected_time in result


class TestFormatVolumeTable:
    """Test cases for format_volume_table function."""

    def test_format_volume_table_empty_list(self):
        """Test format_volume_table with empty volumes list."""
        volumes = []

        result = table_utils.format_volume_table(volumes, show_all=False)

        assert result == 'No existing volumes.'

    def test_format_volume_table_pvc_volumes(self):
        """Test format_volume_table with PVC volumes."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'usedby_pods': ['p1'],
            'last_use': 'sky volumes apply'
        }]

        result = table_utils.format_volume_table(volumes, show_all=False)

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result
        assert 'p1' in result

    def test_format_volume_table_unknown_volume_type(self, monkeypatch):
        """Test format_volume_table with unknown volume type."""
        mock_logger = mock.MagicMock()
        monkeypatch.setattr(table_utils, 'logger', mock_logger)

        volumes = [{
            'name': 'test-volume-1',
            'type': 'unknown-type',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        result = table_utils.format_volume_table(volumes, show_all=False)

        assert result == 'No existing volumes.'
        mock_logger.warning.assert_called_once_with(
            'Unknown volume type: unknown-type')

    def test_format_volume_table_show_all_true(self):
        """Test format_volume_table with show_all=True."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default',
                'storage_class_name': 'gp2',
                'access_mode': 'ReadWriteOnce'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply',
            'usedby_pods': ['p1'],
            'usedby_clusters': ['c1'],
            'name_on_cloud': 'test-volume-1-abc123'
        }]

        result = table_utils.format_volume_table(volumes, show_all=True)

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'test-volume-1-abc123' in result
        assert 'gp2' in result
        assert 'ReadWriteOnce' in result
        assert 'c1' in result


class TestVolumeTableABC:
    """Test cases for VolumeTable abstract base class."""

    def test_volume_table_abc_instantiation(self):
        """Test that VolumeTable ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            table_utils.VolumeTable()

    def test_pvc_volume_table_inheritance(self):
        """Test that PVCVolumeTable properly inherits from VolumeTable."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = table_utils.PVCVolumeTable(volumes, show_all=False)

        assert isinstance(table, table_utils.VolumeTable)
        assert hasattr(table, 'format')
        assert callable(table.format)


class TestRunPodVolumeTable:

    def test_runpod_volume_table_basic(self):
        from sky.client.cli import table_utils as tutils
        volumes = [{
            'name': 'rpv-1',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': 'us',
            'zone': 'iad-1',
            'size': '100',
            'user_name': 'u',
            'workspace': 'w',
            'launched_at': 0,
            'last_attached_at': None,
            'status': 'READY',
            'usedby_clusters': ['c1', 'c2']
        }]
        table = tutils.RunPodVolumeTable(volumes, show_all=False)
        out = table.format()
        assert 'RunPod Network Volumes:' in out
        assert 'rpv-1' in out
        assert 'runpod/us/iad-1' in out
        assert '100Gi' in out
        assert 'c1, c2'[:tutils.constants.USED_BY_TRUNC_LENGTH] in out

    def test_runpod_volume_table_show_all(self):
        from sky.client.cli import table_utils as tutils
        volumes = [{
            'name': 'rpv-2',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': None,
            'zone': 'iad-1',
            'size': '50',
            'user_name': 'u',
            'workspace': 'w',
            'launched_at': 0,
            'last_attached_at': 1234567890,
            'status': 'READY',
            'usedby_pods': ['p1'],
            'name_on_cloud': 'vol-abc'
        }]
        table = tutils.RunPodVolumeTable(volumes, show_all=True)
        out = table.format()
        assert 'RunPod Network Volumes:' in out
        assert 'rpv-2' in out
        assert '/iad-1' in out
        assert 'vol-abc' in out
        assert 'p1' in out

    def test_get_infra_str(self):
        from sky.client.cli.table_utils import _get_infra_str
        assert _get_infra_str(None, None, None) == ''
        assert _get_infra_str('runpod', None, None) == 'runpod'
        assert _get_infra_str('runpod', 'us', None) == 'runpod/us'
        assert _get_infra_str('runpod', 'us', 'iad-1') == 'runpod/us/iad-1'

    def test_format_volume_table_mixed_types_and_separator(self):
        from sky.client.cli.table_utils import format_volume_table
        volumes = [{
            'name': 'p1',
            'type': 'k8s-pvc',
            'cloud': 'kubernetes',
            'region': 'ctx',
            'zone': None,
            'size': '10'
        }, {
            'name': 'r1',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': 'us',
            'zone': 'iad-1',
            'size': '20'
        }]
        out = format_volume_table(volumes, show_all=False)
        # Both headers present and separated by blank line
        assert 'Kubernetes PVCs:' in out
        assert 'RunPod Network Volumes:' in out
        assert '\n\n' in out
