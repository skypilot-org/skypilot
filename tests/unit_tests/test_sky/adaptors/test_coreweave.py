"""Unit tests for CoreWeave cloud adaptor."""

import configparser
import contextlib
import os
import tempfile
import unittest
from unittest import mock

from sky import exceptions
from sky.adaptors import coreweave
from sky.clouds import cloud


class TestCoreWeaveAdaptor(unittest.TestCase):
    """Test cases for CoreWeave adaptor functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear any cached values
        if hasattr(coreweave.session, 'cache_clear'):
            coreweave.session.cache_clear()
        if hasattr(coreweave.resource, 'cache_clear'):
            coreweave.resource.cache_clear()
        if hasattr(coreweave.client, 'cache_clear'):
            coreweave.client.cache_clear()

    def test_load_cw_credentials_env_sets_and_restores_env_vars(self):
        """Test that _load_cw_credentials_env sets and restores environment variables."""
        # Save original values
        original_cred = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
        original_config = os.environ.get('AWS_CONFIG_FILE')

        # Test setting environment variables
        with coreweave._load_cw_credentials_env():
            self.assertEqual(os.environ.get('AWS_SHARED_CREDENTIALS_FILE'),
                             coreweave.COREWEAVE_CREDENTIALS_PATH)
            self.assertEqual(os.environ.get('AWS_CONFIG_FILE'),
                             coreweave.COREWEAVE_CONFIG_PATH)

        # Test restoration
        if original_cred is None:
            self.assertNotIn('AWS_SHARED_CREDENTIALS_FILE', os.environ)
        else:
            self.assertEqual(os.environ.get('AWS_SHARED_CREDENTIALS_FILE'),
                             original_cred)
        if original_config is None:
            self.assertNotIn('AWS_CONFIG_FILE', os.environ)
        else:
            self.assertEqual(os.environ.get('AWS_CONFIG_FILE'), original_config)

    def test_load_cw_credentials_env_preserves_existing_values(self):
        """Test that _load_cw_credentials_env preserves existing environment variables."""
        # Set test values
        os.environ['AWS_SHARED_CREDENTIALS_FILE'] = '/test/creds'
        os.environ['AWS_CONFIG_FILE'] = '/test/config'

        with coreweave._load_cw_credentials_env():
            pass

        # Verify original values are restored
        self.assertEqual(os.environ.get('AWS_SHARED_CREDENTIALS_FILE'),
                         '/test/creds')
        self.assertEqual(os.environ.get('AWS_CONFIG_FILE'), '/test/config')

        # Clean up
        del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        del os.environ['AWS_CONFIG_FILE']

    def test_get_coreweave_credentials_success(self):
        """Test successful retrieval of CoreWeave credentials."""
        # Create mock session with credentials
        mock_session = mock.Mock()
        mock_credentials = mock.Mock()
        mock_frozen = mock.Mock()
        mock_frozen.access_key = 'test_access_key'
        mock_frozen.secret_key = 'test_secret_key'
        mock_credentials.get_frozen_credentials.return_value = mock_frozen
        mock_session.get_credentials.return_value = mock_credentials

        # Mock the context manager
        with mock.patch('sky.adaptors.coreweave._load_cw_credentials_env'
                       ) as mock_load_env:
            mock_load_env.return_value.__enter__ = mock.Mock(return_value=None)
            mock_load_env.return_value.__exit__ = mock.Mock(return_value=None)

            result = coreweave.get_coreweave_credentials(mock_session)

            self.assertEqual(result.access_key, 'test_access_key')
            self.assertEqual(result.secret_key, 'test_secret_key')
            mock_session.get_credentials.assert_called_once()

    def test_get_coreweave_credentials_not_found(self):
        """Test error when CoreWeave credentials are not found."""
        # Create mock session with no credentials
        mock_session = mock.Mock()
        mock_session.get_credentials.return_value = None

        # Mock the context manager to return itself
        with mock.patch('sky.adaptors.coreweave._load_cw_credentials_env'
                       ) as mock_load_env:
            mock_load_env.return_value.__enter__ = mock.Mock(return_value=None)
            mock_load_env.return_value.__exit__ = mock.Mock(return_value=None)

            with self.assertRaises(ValueError) as context:
                coreweave.get_coreweave_credentials(mock_session)

            self.assertIn('CoreWeave credentials not found',
                          str(context.exception))

    def test_get_endpoint_default(self):
        """Test get_endpoint returns default endpoint when config file doesn't exist."""
        with mock.patch('os.path.isfile', return_value=False):
            endpoint = coreweave.get_endpoint()
            self.assertEqual(endpoint, coreweave._DEFAULT_ENDPOINT)

    def test_get_endpoint_from_config_file(self):
        """Test get_endpoint parses endpoint from config file."""
        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.config') as f:
            f.write('[profile cw]\n')
            f.write('endpoint_url = https://custom-endpoint.com\n')
            f.write('s3 =\n')
            f.write('    addressing_style = virtual\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CONFIG_PATH',
                            temp_path):
                endpoint = coreweave.get_endpoint()
                self.assertEqual(endpoint, 'https://custom-endpoint.com')
        finally:
            os.unlink(temp_path)

    def test_get_endpoint_with_invalid_config(self):
        """Test get_endpoint returns default when config file is invalid."""
        # Create temporary invalid config file
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.config') as f:
            f.write('invalid config content [[[')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CONFIG_PATH',
                            temp_path):
                endpoint = coreweave.get_endpoint()
                self.assertEqual(endpoint, coreweave._DEFAULT_ENDPOINT)
        finally:
            os.unlink(temp_path)

    def test_get_endpoint_missing_endpoint_url(self):
        """Test get_endpoint returns default when endpoint_url is missing."""
        # Create temporary config file without endpoint_url
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.config') as f:
            f.write('[profile cw]\n')
            f.write('s3 =\n')
            f.write('    addressing_style = virtual\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CONFIG_PATH',
                            temp_path):
                endpoint = coreweave.get_endpoint()
                self.assertEqual(endpoint, coreweave._DEFAULT_ENDPOINT)
        finally:
            os.unlink(temp_path)

    def test_coreweave_profile_in_config_exists(self):
        """Test coreweave_profile_in_config returns True when profile exists."""
        # Create temporary config file with profile
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.config') as f:
            f.write('[profile cw]\n')
            f.write('endpoint_url = https://cwobject.com\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CONFIG_PATH',
                            temp_path):
                result = coreweave.coreweave_profile_in_config()
                self.assertTrue(result)
        finally:
            os.unlink(temp_path)

    def test_coreweave_profile_in_config_not_exists(self):
        """Test coreweave_profile_in_config returns False when profile doesn't exist."""
        # Create temporary config file without cw profile
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.config') as f:
            f.write('[profile default]\n')
            f.write('region = us-east-1\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CONFIG_PATH',
                            temp_path):
                result = coreweave.coreweave_profile_in_config()
                self.assertFalse(result)
        finally:
            os.unlink(temp_path)

    def test_coreweave_profile_in_config_file_not_exists(self):
        """Test coreweave_profile_in_config returns False when file doesn't exist."""
        with mock.patch('os.path.isfile', return_value=False):
            result = coreweave.coreweave_profile_in_config()
            self.assertFalse(result)

    def test_coreweave_profile_in_cred_exists(self):
        """Test coreweave_profile_in_cred returns True when profile exists."""
        # Create temporary credentials file with profile
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.credentials') as f:
            f.write('[cw]\n')
            f.write('aws_access_key_id = test_key\n')
            f.write('aws_secret_access_key = test_secret\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CREDENTIALS_PATH',
                            temp_path):
                result = coreweave.coreweave_profile_in_cred()
                self.assertTrue(result)
        finally:
            os.unlink(temp_path)

    def test_coreweave_profile_in_cred_not_exists(self):
        """Test coreweave_profile_in_cred returns False when profile doesn't exist."""
        # Create temporary credentials file without cw profile
        with tempfile.NamedTemporaryFile(mode='w',
                                         delete=False,
                                         suffix='.credentials') as f:
            f.write('[default]\n')
            f.write('aws_access_key_id = test_key\n')
            temp_path = f.name

        try:
            with mock.patch('sky.adaptors.coreweave.COREWEAVE_CREDENTIALS_PATH',
                            temp_path):
                result = coreweave.coreweave_profile_in_cred()
                self.assertFalse(result)
        finally:
            os.unlink(temp_path)

    def test_coreweave_profile_in_cred_file_not_exists(self):
        """Test coreweave_profile_in_cred returns False when file doesn't exist."""
        with mock.patch('os.path.isfile', return_value=False):
            result = coreweave.coreweave_profile_in_cred()
            self.assertFalse(result)

    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_config')
    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_cred')
    def test_check_storage_credentials_success(self, mock_cred, mock_config):
        """Test check_storage_credentials returns True when both profiles exist."""
        mock_cred.return_value = True
        mock_config.return_value = True

        result, hints = coreweave.check_storage_credentials()

        self.assertTrue(result)
        self.assertIsNone(hints)

    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_config')
    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_cred')
    def test_check_storage_credentials_missing_cred(self, mock_cred,
                                                    mock_config):
        """Test check_storage_credentials returns False when credentials profile is missing."""
        mock_cred.return_value = False
        mock_config.return_value = True

        result, hints = coreweave.check_storage_credentials()

        self.assertFalse(result)
        self.assertIsNotNone(hints)
        self.assertIn('profile is not set', hints)
        self.assertIn(coreweave.COREWEAVE_CREDENTIALS_PATH, hints)

    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_config')
    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_cred')
    def test_check_storage_credentials_missing_config(self, mock_cred,
                                                      mock_config):
        """Test check_storage_credentials returns False when config profile is missing."""
        mock_cred.return_value = True
        mock_config.return_value = False

        result, hints = coreweave.check_storage_credentials()

        self.assertFalse(result)
        self.assertIsNotNone(hints)
        self.assertIn('profile is not set', hints)
        self.assertIn(coreweave.COREWEAVE_CONFIG_PATH, hints)

    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_config')
    @mock.patch('sky.adaptors.coreweave.coreweave_profile_in_cred')
    def test_check_storage_credentials_missing_both(self, mock_cred,
                                                    mock_config):
        """Test check_storage_credentials returns False when both profiles are missing."""
        mock_cred.return_value = False
        mock_config.return_value = False

        result, hints = coreweave.check_storage_credentials()

        self.assertFalse(result)
        self.assertIsNotNone(hints)
        self.assertIn('profile is not set', hints)
        self.assertIn(coreweave.COREWEAVE_CREDENTIALS_PATH, hints)
        self.assertIn(coreweave.COREWEAVE_CONFIG_PATH, hints)
        self.assertIn('Additionally', hints)

    @mock.patch('sky.adaptors.coreweave.check_storage_credentials')
    def test_check_credentials_storage(self, mock_check_storage):
        """Test check_credentials delegates to check_storage_credentials for STORAGE capability."""
        mock_check_storage.return_value = (True, None)

        result, hints = coreweave.check_credentials(
            cloud.CloudCapability.STORAGE)

        self.assertTrue(result)
        self.assertIsNone(hints)
        mock_check_storage.assert_called_once()

    def test_check_credentials_unsupported_capability(self):
        """Test check_credentials raises error for unsupported capabilities."""
        with self.assertRaises(exceptions.NotSupportedError) as context:
            coreweave.check_credentials(cloud.CloudCapability.COMPUTE)

        self.assertIn('does not support', str(context.exception))

    def test_get_credential_file_mounts(self):
        """Test get_credential_file_mounts returns correct file mount mappings."""
        mounts = coreweave.get_credential_file_mounts()

        self.assertIsInstance(mounts, dict)
        self.assertEqual(len(mounts), 2)
        self.assertIn(coreweave.COREWEAVE_CREDENTIALS_PATH, mounts)
        self.assertIn(coreweave.COREWEAVE_CONFIG_PATH, mounts)
        self.assertEqual(mounts[coreweave.COREWEAVE_CREDENTIALS_PATH],
                         coreweave.COREWEAVE_CREDENTIALS_PATH)
        self.assertEqual(mounts[coreweave.COREWEAVE_CONFIG_PATH],
                         coreweave.COREWEAVE_CONFIG_PATH)

    @mock.patch('sky.adaptors.coreweave.session')
    @mock.patch('sky.adaptors.coreweave.get_coreweave_credentials')
    @mock.patch('sky.adaptors.coreweave.get_endpoint')
    def test_resource_creation(self, mock_get_endpoint, mock_get_creds,
                               mock_session):
        """Test resource creation with proper credentials and endpoint."""
        # Setup mocks
        mock_session_obj = mock.Mock()
        mock_session.return_value = mock_session_obj

        mock_creds = mock.Mock()
        mock_creds.access_key = 'test_access_key'
        mock_creds.secret_key = 'test_secret_key'
        mock_get_creds.return_value = mock_creds

        mock_get_endpoint.return_value = 'https://test-endpoint.com'

        mock_resource = mock.Mock()
        mock_session_obj.resource.return_value = mock_resource

        # Call resource function
        result = coreweave.resource('s3')

        # Verify
        mock_session_obj.resource.assert_called_once()
        call_args = mock_session_obj.resource.call_args
        self.assertEqual(call_args[0][0], 's3')
        self.assertEqual(call_args[1]['endpoint_url'],
                         'https://test-endpoint.com')
        self.assertEqual(call_args[1]['aws_access_key_id'], 'test_access_key')
        self.assertEqual(call_args[1]['aws_secret_access_key'],
                         'test_secret_key')
        self.assertEqual(call_args[1]['region_name'], 'auto')

    @mock.patch('sky.adaptors.coreweave.session')
    @mock.patch('sky.adaptors.coreweave.get_coreweave_credentials')
    @mock.patch('sky.adaptors.coreweave.get_endpoint')
    def test_client_creation(self, mock_get_endpoint, mock_get_creds,
                             mock_session):
        """Test client creation with proper credentials and endpoint."""
        # Setup mocks
        mock_session_obj = mock.Mock()
        mock_session.return_value = mock_session_obj

        mock_creds = mock.Mock()
        mock_creds.access_key = 'test_access_key'
        mock_creds.secret_key = 'test_secret_key'
        mock_get_creds.return_value = mock_creds

        mock_get_endpoint.return_value = 'https://test-endpoint.com'

        mock_client = mock.Mock()
        mock_session_obj.client.return_value = mock_client

        # Call client function
        result = coreweave.client('s3')

        # Verify
        mock_session_obj.client.assert_called_once()
        call_args = mock_session_obj.client.call_args
        self.assertEqual(call_args[0][0], 's3')
        self.assertEqual(call_args[1]['endpoint_url'],
                         'https://test-endpoint.com')
        self.assertEqual(call_args[1]['aws_access_key_id'], 'test_access_key')
        self.assertEqual(call_args[1]['aws_secret_access_key'],
                         'test_secret_key')
        self.assertEqual(call_args[1]['region_name'], 'auto')
