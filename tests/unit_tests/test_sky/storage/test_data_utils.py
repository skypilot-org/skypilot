"""Unit tests for sky.data.data_utils module."""
from unittest import mock

import pytest

from sky.adaptors import coreweave
from sky.data import data_utils


class TestSplitCoreweavePath:
    """Tests for split_coreweave_path function."""

    def test_basic_path_with_key(self):
        """Test splitting a basic CoreWeave path with bucket and key."""
        bucket, key = data_utils.split_coreweave_path('cw://imagenet/train/')
        assert bucket == 'imagenet'
        assert key == 'train/'

    def test_path_with_nested_directories(self):
        """Test splitting path with multiple nested directories."""
        bucket, key = data_utils.split_coreweave_path(
            'cw://my-bucket/path/to/file.txt')
        assert bucket == 'my-bucket'
        assert key == 'path/to/file.txt'

    def test_bucket_only(self):
        """Test splitting path with bucket only (no key)."""
        bucket, key = data_utils.split_coreweave_path('cw://my-bucket')
        assert bucket == 'my-bucket'
        assert key == ''

    def test_bucket_with_trailing_slash(self):
        """Test splitting path with bucket and trailing slash."""
        bucket, key = data_utils.split_coreweave_path('cw://my-bucket/')
        assert bucket == 'my-bucket'
        assert key == ''

    def test_deep_nested_path(self):
        """Test splitting deeply nested path."""
        bucket, key = data_utils.split_coreweave_path(
            'cw://bucket/dir1/dir2/dir3/dir4/file.ext')
        assert bucket == 'bucket'
        assert key == 'dir1/dir2/dir3/dir4/file.ext'

    def test_path_with_special_characters(self):
        """Test splitting path with special characters in key."""
        bucket, key = data_utils.split_coreweave_path(
            'cw://bucket/path-with_special.chars/file_v2.0.txt')
        assert bucket == 'bucket'
        assert key == 'path-with_special.chars/file_v2.0.txt'

    def test_bucket_with_hyphens(self):
        """Test splitting path with hyphens in bucket name."""
        bucket, key = data_utils.split_coreweave_path(
            'cw://my-test-bucket/path/to/file')
        assert bucket == 'my-test-bucket'
        assert key == 'path/to/file'


class TestVerifyCoreweaveBucket:
    """Tests for verify_coreweave_bucket function."""

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    def test_successful_verification_no_retry(self, mock_create_client):
        """Test successful bucket verification on first attempt."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client
        mock_client.head_bucket.return_value = {}

        result = data_utils.verify_coreweave_bucket('test-bucket')

        assert result is True
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    def test_access_denied_403(self, mock_create_client):
        """Test bucket verification with 403 access denied error."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Create mock ClientError
        error_response = {'Error': {'Code': '403'}}
        mock_error = coreweave.botocore_exceptions().ClientError(
            error_response, 'head_bucket')
        mock_client.head_bucket.side_effect = mock_error

        result = data_utils.verify_coreweave_bucket('test-bucket')

        assert result is False
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_bucket_not_found_404_no_retry(self, mock_sleep,
                                           mock_create_client):
        """Test bucket verification with 404 error and no retry."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Create mock ClientError for 404
        error_response = {'Error': {'Code': '404'}}
        mock_error = coreweave.botocore_exceptions().ClientError(
            error_response, 'head_bucket')
        mock_client.head_bucket.side_effect = mock_error

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=0)

        assert result is False
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')
        mock_sleep.assert_not_called()

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_retry_mechanism_success_after_retries(self, mock_sleep,
                                                   mock_create_client):
        """Test retry mechanism succeeds after a few retries."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Fail twice, then succeed
        error_response = {'Error': {'Code': '404'}}
        mock_error = coreweave.botocore_exceptions().ClientError(
            error_response, 'head_bucket')
        mock_client.head_bucket.side_effect = [mock_error, mock_error, {}]

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=3)

        assert result is True
        assert mock_client.head_bucket.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_retry_mechanism_all_retries_exhausted(self, mock_sleep,
                                                   mock_create_client):
        """Test retry mechanism when all retries are exhausted."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Always fail
        error_response = {'Error': {'Code': '404'}}
        mock_error = coreweave.botocore_exceptions().ClientError(
            error_response, 'head_bucket')
        mock_client.head_bucket.side_effect = mock_error

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=2)

        assert result is False
        # Should try 3 times (retry=2 means 3 total attempts)
        assert mock_client.head_bucket.call_count == 3
        # Should sleep 2 times (between the 3 attempts)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_unexpected_client_error(self, mock_sleep, mock_create_client):
        """Test handling of unexpected ClientError codes."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Unexpected error code
        error_response = {'Error': {'Code': '500'}}
        mock_error = coreweave.botocore_exceptions().ClientError(
            error_response, 'head_bucket')
        mock_client.head_bucket.side_effect = mock_error

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=0)

        assert result is False
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_unexpected_exception(self, mock_sleep, mock_create_client):
        """Test handling of unexpected non-ClientError exceptions."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Some unexpected exception
        mock_client.head_bucket.side_effect = ValueError('Unexpected error')

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=0)

        assert result is False
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    @mock.patch('time.sleep')
    def test_retry_with_unexpected_exception_then_success(
            self, mock_sleep, mock_create_client):
        """Test retry mechanism with unexpected exception then success."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client

        # Fail with unexpected error, then succeed
        mock_client.head_bucket.side_effect = [ValueError('Network error'), {}]

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=2)

        assert result is True
        assert mock_client.head_bucket.call_count == 2
        mock_sleep.assert_called_once_with(5)

    @mock.patch('sky.data.data_utils.create_coreweave_client')
    def test_zero_retry_parameter(self, mock_create_client):
        """Test with explicit retry=0 parameter."""
        mock_client = mock.MagicMock()
        mock_create_client.return_value = mock_client
        mock_client.head_bucket.return_value = {}

        result = data_utils.verify_coreweave_bucket('test-bucket', retry=0)

        assert result is True
        mock_client.head_bucket.assert_called_once_with(Bucket='test-bucket')
