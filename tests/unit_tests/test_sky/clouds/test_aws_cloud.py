"""Test the AWS class."""

import unittest.mock as mock

import pytest

from sky.clouds import aws as aws_mod


class TestGetImageRootDeviceName:

    @pytest.fixture(autouse=True)
    def reset_logger(self):
        # Ensure logger is available
        yield

    def test_skypilot_image_returns_default(self):
        result = aws_mod.AWS.get_image_root_device_name('skypilot:ubuntu-22.04',
                                                        'us-east-1')
        assert result == aws_mod.DEFAULT_ROOT_DEVICE_NAME

    def test_missing_region_assertion(self):
        with pytest.raises(AssertionError):
            aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef0',
                                                   None)

    @mock.patch.object(aws_mod, 'aws')
    def test_returns_root_device_name_when_present(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [{
                'ImageId': 'ami-0123456789abcdef0',
                'RootDeviceName': '/dev/xvda'
            }],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        result = aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef0',
                                                        'us-west-2')
        assert result == '/dev/xvda'
        mock_aws.client.assert_called_with('ec2', region_name='us-west-2')

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_missing_root_device_name_debugs_and_returns_default(
            self, mock_aws, mock_logger):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [{
                'ImageId': 'ami-0123456789abcdef1',
                # No 'RootDeviceName'
            }],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        result = aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef1',
                                                        'us-west-2')
        assert result == aws_mod.DEFAULT_ROOT_DEVICE_NAME
        assert mock_logger.debug.called

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_no_credentials_fallback_default(self, mock_aws, mock_logger):
        # Simulate NoCredentialsError
        class DummyExc(Exception):
            pass

        # Build a dummy exceptions namespace matching access pattern
        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.NoCredentialsError(
        )

        result = aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef2',
                                                        'eu-central-1')
        assert result == aws_mod.DEFAULT_ROOT_DEVICE_NAME
        assert mock_logger.debug.called
        # Verify the debug log message contains expected content
        debug_call_args = mock_logger.debug.call_args[0][0]
        assert 'Failed to get image root device name' in debug_call_args
        assert 'ami-0123456789abcdef2' in debug_call_args
        assert 'eu-central-1' in debug_call_args

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_client_error_raises_value_error(self, mock_aws, mock_logger):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):

                def __init__(self, message):
                    self.message = message
                    super().__init__(message)

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.ClientError(
            'Image not found')

        with pytest.raises(ValueError) as ei:
            aws_mod.AWS.get_image_root_device_name('ami-0deadbeef',
                                                   'ap-south-1')
        assert 'not found' in str(ei.value)
        # Verify the debug log was called
        assert mock_logger.debug.called
        debug_call_args = mock_logger.debug.call_args_list[0][0][0]
        assert 'Failed to get image root device name' in debug_call_args
        assert 'ami-0deadbeef' in debug_call_args
        assert 'ap-south-1' in debug_call_args

    @mock.patch.object(aws_mod, 'aws')
    def test_image_not_found_raises_value_error(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }

        # Ensure botocore_exceptions returns real exception classes (not mocks)
        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        with pytest.raises(ValueError) as ei:
            aws_mod.AWS.get_image_root_device_name('ami-00000000', 'us-east-2')
        assert 'Image' in str(ei.value)


class TestGetImageSize:

    def test_skypilot_image_returns_default(self):
        result = aws_mod.AWS.get_image_size('skypilot:ubuntu-22.04',
                                            'us-east-1')
        assert result == aws_mod.DEFAULT_AMI_GB

    def test_missing_region_assertion(self):
        with pytest.raises(AssertionError):
            aws_mod.AWS.get_image_size('ami-0123456789abcdef0', None)

    @mock.patch.object(aws_mod, 'aws')
    def test_returns_image_size_when_found(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [{
                'ImageId': 'ami-0123456789abcdef0',
                'BlockDeviceMappings': [{
                    'Ebs': {
                        'VolumeSize': 100
                    }
                }]
            }],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        result = aws_mod.AWS.get_image_size('ami-0123456789abcdef0',
                                            'us-west-2')
        assert result == 100
        mock_aws.client.assert_called_with('ec2', region_name='us-west-2')

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_no_credentials_fallback_default(self, mock_aws, mock_logger):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.NoCredentialsError(
            'No credentials')

        result = aws_mod.AWS.get_image_size('ami-0123456789abcdef1',
                                            'eu-central-1')
        assert result == aws_mod.DEFAULT_AMI_GB
        # Verify the debug log was called
        assert mock_logger.debug.called
        debug_call_args = mock_logger.debug.call_args[0][0]
        assert 'Failed to get image size' in debug_call_args
        assert 'ami-0123456789abcdef1' in debug_call_args
        assert 'eu-central-1' in debug_call_args

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_profile_not_found_fallback_default(self, mock_aws, mock_logger):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.ProfileNotFound(
            'Profile not found')

        result = aws_mod.AWS.get_image_size('ami-0123456789abcdef2',
                                            'ap-south-1')
        assert result == aws_mod.DEFAULT_AMI_GB
        # Verify the debug log was called
        assert mock_logger.debug.called
        debug_call_args = mock_logger.debug.call_args[0][0]
        assert 'Failed to get image size' in debug_call_args
        assert 'ami-0123456789abcdef2' in debug_call_args
        assert 'ap-south-1' in debug_call_args

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_client_error_raises_value_error(self, mock_aws, mock_logger):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):

                def __init__(self, message):
                    self.message = message
                    super().__init__(message)

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.ClientError(
            'Image not found')

        with pytest.raises(ValueError) as ei:
            aws_mod.AWS.get_image_size('ami-0deadbeef', 'us-east-1')
        assert 'not found' in str(ei.value)
        # Verify the debug log was called
        assert mock_logger.debug.called
        debug_call_args = mock_logger.debug.call_args_list[0][0][0]
        assert 'Failed to get image size' in debug_call_args
        assert 'ami-0deadbeef' in debug_call_args
        assert 'us-east-1' in debug_call_args

    @mock.patch.object(aws_mod, 'aws')
    def test_image_not_found_raises_value_error(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }

        # Ensure botocore_exceptions returns real exception classes (not mocks)
        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions

        with pytest.raises(ValueError) as ei:
            aws_mod.AWS.get_image_size('ami-00000000', 'us-east-2')
        assert 'Image' in str(ei.value)


class TestEfaHelpers:

    def test_is_efa_instance_type(self):
        # True for EFA families
        assert aws_mod._is_efa_instance_type('g6.12xlarge') is True
        assert aws_mod._is_efa_instance_type('p5.48xlarge') is True
        assert aws_mod._is_efa_instance_type('p6-b200.24xlarge') is True
        # False for non-EFA families
        assert aws_mod._is_efa_instance_type('c5.2xlarge') is False
        assert aws_mod._is_efa_instance_type('t3.micro') is False

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_returns_latest_available(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [
                {
                    'ImageId': 'ami-old',
                    'State': 'available',
                    'CreationDate': '2024-01-01T00:00:00.000Z',
                },
                {
                    'ImageId': 'ami-new',
                    'State': 'available',
                    'CreationDate': '2024-06-01T00:00:00.000Z',
                },
                {
                    'ImageId': 'ami-pending',
                    'State': 'pending',
                    'CreationDate': '2024-07-01T00:00:00.000Z',
                },
            ]
        }
        result = aws_mod._get_efa_image_id('us-west-2')
        assert result == 'ami-new'
        mock_aws.client.assert_called_with('ec2', region_name='us-west-2')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_no_credentials_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.NoCredentialsError(
        )

        with pytest.raises(ValueError):
            aws_mod._get_efa_image_id('us-east-1')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_client_error_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.ClientError()

        with pytest.raises(ValueError):
            aws_mod._get_efa_image_id('eu-central-1')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_non_efa_type_short_circuit(self, mock_aws):
        # Should return 0 and not call aws.client at all
        result = aws_mod._get_max_efa_interfaces('c5.2xlarge', 'us-east-1')
        assert result == 0
        mock_aws.client.assert_not_called()

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_success(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.return_value = {
            'InstanceTypes': [{
                'NetworkInfo': {
                    'EfaInfo': {
                        'MaximumEfaInterfaces': 8,
                    }
                }
            }]
        }
        result = aws_mod._get_max_efa_interfaces('g6.8xlarge', 'us-west-2')
        assert result == 8
        mock_aws.client.assert_called_with('ec2', region_name='us-west-2')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_no_creds_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.side_effect = DummyExceptions.NoCredentialsError(
        )

        with pytest.raises(ValueError):
            aws_mod._get_max_efa_interfaces('g6.12xlarge', 'ap-south-1')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_client_error_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.side_effect = DummyExceptions.ClientError(
        )

        with pytest.raises(ValueError):
            aws_mod._get_max_efa_interfaces('g6.12xlarge', 'eu-west-1')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_missing_images_key_returns_none(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        assert aws_mod._get_efa_image_id('us-east-1') is None

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_empty_images_returns_none(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        assert aws_mod._get_efa_image_id('us-east-2') is None

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_no_available_returns_none(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [
                {
                    'ImageId': 'ami-1',
                    'State': 'pending',
                    'CreationDate': '2024-01-01T00:00:00.000Z',
                },
                {
                    'ImageId': 'ami-2',
                    'State': 'deregistered',
                    'CreationDate': '2024-02-01T00:00:00.000Z',
                },
            ],
            'ResponseMetadata': {
                'HTTPStatusCode': 200,
                'RetryAttempts': 0,
                'NextToken': None,
            },
        }
        assert aws_mod._get_efa_image_id('us-west-1') is None

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_profile_not_found_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.side_effect = DummyExceptions.ProfileNotFound()
        with pytest.raises(ValueError):
            aws_mod._get_efa_image_id('ap-northeast-1')

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_missing_instance_types_returns_zero(
            self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.return_value = {}
        assert aws_mod._get_max_efa_interfaces('g6.12xlarge', 'us-east-1') == 0

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_empty_instance_types_returns_zero(
            self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.return_value = {'InstanceTypes': []}
        assert aws_mod._get_max_efa_interfaces('g6.24xlarge', 'us-west-1') == 0

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_no_efa_info_returns_zero(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.return_value = {
            'InstanceTypes': [{
                'NetworkInfo': {}
            }]
        }
        assert aws_mod._get_max_efa_interfaces('g6.48xlarge', 'us-west-2') == 0

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_missing_max_field_returns_zero(
            self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.return_value = {
            'InstanceTypes': [{
                'NetworkInfo': {
                    'EfaInfo': {}
                }
            }]
        }
        assert aws_mod._get_max_efa_interfaces('g6.48xlarge',
                                               'eu-central-1') == 0

    @mock.patch.object(aws_mod, 'aws')
    def test_get_max_efa_interfaces_profile_not_found_raises(self, mock_aws):

        class DummyExceptions:

            class NoCredentialsError(Exception):
                pass

            class ProfileNotFound(Exception):
                pass

            class ClientError(Exception):
                pass

        mock_aws.botocore_exceptions.return_value = DummyExceptions
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_instance_types.side_effect = DummyExceptions.ProfileNotFound(
        )
        with pytest.raises(ValueError):
            aws_mod._get_max_efa_interfaces('g6.12xlarge', 'ap-northeast-1')


class TestAwsConfigureList:

    @mock.patch('sky.adaptors.aws.get_workspace_profile')
    @mock.patch('subprocess.run')
    def test_command_generation_with_profiles(self, mock_run, mock_get_profile):
        """Test command generation with no profile, with profile, and different profiles."""
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = b'output'
        mock_run.return_value = mock_result

        aws_mod.AWS._aws_configure_list.cache_clear()

        # Test with no profile
        mock_get_profile.return_value = None
        aws_mod.AWS._aws_configure_list()
        assert mock_run.call_args[0][0] == 'aws configure list'

        # Test with profile
        mock_get_profile.return_value = 'dev'
        aws_mod.AWS._aws_configure_list()
        assert mock_run.call_args[0][0] == 'aws configure list --profile dev'

        # Test with different profiles
        mock_get_profile.return_value = 'profile1'
        aws_mod.AWS._aws_configure_list()
        assert mock_run.call_args[0][
            0] == 'aws configure list --profile profile1'

    @mock.patch('sky.adaptors.aws.get_workspace_profile')
    @mock.patch('subprocess.run')
    def test_caching_behavior(self, mock_run, mock_get_profile):
        """Test caching: same profile cached, different profiles not cached."""
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = b'output'
        mock_run.return_value = mock_result

        aws_mod.AWS._aws_configure_list.cache_clear()

        # Same profile should be cached
        mock_get_profile.return_value = 'test'
        aws_mod.AWS._aws_configure_list()
        aws_mod.AWS._aws_configure_list()
        assert mock_run.call_count == 1

        # Different profiles should NOT be cached together
        mock_get_profile.return_value = 'other'
        aws_mod.AWS._aws_configure_list()
        assert mock_run.call_count == 2


class TestAwsProfileAwareLruCache:
    """Tests for aws_profile_aware_lru_cache decorator."""

    def test_cache_distinguishes_by_aws_profile(self):
        """Test that cache differentiates between different AWS profiles."""
        import os

        from sky import skypilot_config
        from sky.clouds.aws import aws_profile_aware_lru_cache

        call_count = 0

        @aws_profile_aware_lru_cache(scope='request', maxsize=5)
        def expensive_func(cls):
            nonlocal call_count
            call_count += 1
            return f"result_{call_count}"

        expensive_func.cache_clear()

        # Set config file with multiple workspaces and profiles
        old_config_path = os.environ.get(
            skypilot_config.ENV_VAR_SKYPILOT_CONFIG, None)
        try:
            os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = \
                './tests/test_yamls/test_aws_profile_workspace_config.yaml'
            skypilot_config.reload_config()

            # Call with workspace-a
            with skypilot_config.local_active_workspace_ctx('workspace-a'):
                result = expensive_func(None)
                assert result == 'result_1'
                assert call_count == 1

                # Same workspace should use cache
                result = expensive_func(None)
                assert result == 'result_1'
                assert call_count == 1

            # Call with workspace-b
            with skypilot_config.local_active_workspace_ctx('workspace-b'):
                result = expensive_func(None)
                assert result == 'result_2'
                assert call_count == 2  # Called again for different workspace

                # Same workspace (workspace-b) should use cache
                result = expensive_func(None)
                assert result == 'result_2'
                assert call_count == 2

            with skypilot_config.local_active_workspace_ctx('workspace-a'):
                # Back to workspace-a should use its cached result
                result = expensive_func(None)
                assert result == 'result_1'
                assert call_count == 2

                # Clear cache
                expensive_func.cache_clear()
                result = expensive_func(None)
                assert result == 'result_3'
                assert call_count == 3

                result = expensive_func(None)
                assert result == 'result_3'
                assert call_count == 3
        finally:
            # Restore original config
            if old_config_path:
                os.environ[
                    skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = old_config_path
            else:
                os.environ.pop(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, None)
            skypilot_config.reload_config()


class TestAwsConfigFileEnvVar:
    """Tests for AWS_CONFIG_FILE credential override."""

    def test_get_credential_file_mounts_respects_env_override(
            self, tmp_path, monkeypatch):
        credential_file = tmp_path / 'aws_credentials'
        credential_file.write_text('dummy')
        monkeypatch.setenv('AWS_CONFIG_FILE', str(credential_file))

        aws = aws_mod.AWS()
        with mock.patch.object(
                aws_mod.AWS,
                '_current_identity_type',
                return_value=aws_mod.AWSIdentityType.SHARED_CREDENTIALS_FILE):
            mounts = aws.get_credential_file_mounts()

        assert mounts == {
            aws_mod._DEFAULT_AWS_CONFIG_PATH: str(credential_file)
        }
