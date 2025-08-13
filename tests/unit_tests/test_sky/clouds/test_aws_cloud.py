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
