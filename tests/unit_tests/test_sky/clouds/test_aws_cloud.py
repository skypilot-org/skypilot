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
            }]
        }
        result = aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef0',
                                                        'us-west-2')
        assert result == '/dev/xvda'
        mock_aws.client.assert_called_with('ec2', region_name='us-west-2')

    @mock.patch.object(aws_mod, 'logger')
    @mock.patch.object(aws_mod, 'aws')
    def test_missing_root_device_name_warns_and_returns_default(
            self, mock_aws, mock_logger):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {
            'Images': [{
                'ImageId': 'ami-0123456789abcdef1',
                # No 'RootDeviceName'
            }]
        }
        result = aws_mod.AWS.get_image_root_device_name('ami-0123456789abcdef1',
                                                        'us-west-2')
        assert result == aws_mod.DEFAULT_ROOT_DEVICE_NAME
        assert mock_logger.warning.called

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
        assert mock_logger.warning.called

    @mock.patch.object(aws_mod, 'aws')
    def test_client_error_raises_value_error(self, mock_aws):

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

        with pytest.raises(ValueError) as ei:
            aws_mod.AWS.get_image_root_device_name('ami-0deadbeef',
                                                   'ap-south-1')
        assert 'not found' in str(ei.value)

    @mock.patch.object(aws_mod, 'aws')
    def test_image_not_found_raises_value_error(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {'Images': []}

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
        client.describe_images.return_value = {}
        assert aws_mod._get_efa_image_id('us-east-1') is None

    @mock.patch.object(aws_mod, 'aws')
    def test_get_efa_image_id_empty_images_returns_none(self, mock_aws):
        client = mock.Mock()
        mock_aws.client.return_value = client
        client.describe_images.return_value = {'Images': []}
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
            ]
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
