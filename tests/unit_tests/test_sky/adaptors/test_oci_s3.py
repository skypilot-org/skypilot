"""Unit tests for the OCI S3-compatible API adaptor."""

import os
import tempfile
import unittest
from unittest import mock

from sky import exceptions
from sky.adaptors import oci_s3
from sky.clouds import cloud


class TestOciS3Adaptor(unittest.TestCase):
    """Test cases for OCI S3 adaptor functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear any cached values
        if hasattr(oci_s3.session, 'cache_clear'):
            oci_s3.session.cache_clear()
        if hasattr(oci_s3.resource, 'cache_clear'):
            oci_s3.resource.cache_clear()
        if hasattr(oci_s3.client, 'cache_clear'):
            oci_s3.client.cache_clear()

    def _write_temp_file(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(content)
            return f.name

    def test_load_oci_s3_credentials_env_sets_and_restores_env_vars(self):
        original_cred = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
        original_config = os.environ.get('AWS_CONFIG_FILE')

        with oci_s3._load_oci_s3_credentials_env():
            self.assertEqual(os.environ.get('AWS_SHARED_CREDENTIALS_FILE'),
                             oci_s3.OCI_S3_CREDENTIALS_PATH)
            self.assertEqual(os.environ.get('AWS_CONFIG_FILE'),
                             oci_s3.OCI_S3_CONFIG_PATH)

        if original_cred is None:
            self.assertNotIn('AWS_SHARED_CREDENTIALS_FILE', os.environ)
        else:
            self.assertEqual(os.environ.get('AWS_SHARED_CREDENTIALS_FILE'),
                             original_cred)
        if original_config is None:
            self.assertNotIn('AWS_CONFIG_FILE', os.environ)
        else:
            self.assertEqual(os.environ.get('AWS_CONFIG_FILE'), original_config)

    def test_use_s3_api_false_when_files_missing(self):
        with mock.patch('os.path.isfile', return_value=False):
            self.assertFalse(oci_s3.use_s3_api())

    def test_use_s3_api_true_when_both_profiles_exist(self):
        cred_path = self._write_temp_file(
            '[oci]\n'
            'aws_access_key_id = test_key\n'
            'aws_secret_access_key = test_secret\n')
        config_path = self._write_temp_file(
            '[profile oci]\n'
            'endpoint_url = https://ns.compat.objectstorage.us-sanjose-1'
            '.oci.customer-oci.com\n'
            'region = us-sanjose-1\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CREDENTIALS_PATH',
                            cred_path), \
                 mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            config_path):
                self.assertTrue(oci_s3.use_s3_api())
        finally:
            os.unlink(cred_path)
            os.unlink(config_path)

    def test_use_s3_api_false_when_only_credentials_exist(self):
        cred_path = self._write_temp_file(
            '[oci]\n'
            'aws_access_key_id = test_key\n'
            'aws_secret_access_key = test_secret\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CREDENTIALS_PATH',
                            cred_path), \
                 mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            '/nonexistent/oci/s3.config'):
                self.assertFalse(oci_s3.use_s3_api())
        finally:
            os.unlink(cred_path)

    def test_get_endpoint_raises_when_config_missing(self):
        with mock.patch('os.path.isfile', return_value=False):
            with self.assertRaises(ValueError) as context:
                oci_s3.get_endpoint()
            self.assertIn('endpoint not found', str(context.exception))

    def test_get_endpoint_raises_when_endpoint_url_missing(self):
        config_path = self._write_temp_file('[profile oci]\n'
                                            'region = us-sanjose-1\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            config_path):
                with self.assertRaises(ValueError):
                    oci_s3.get_endpoint()
        finally:
            os.unlink(config_path)

    def test_get_endpoint_from_config_file(self):
        endpoint = ('https://ns.compat.objectstorage.us-sanjose-1'
                    '.oci.customer-oci.com')
        config_path = self._write_temp_file('[profile oci]\n'
                                            f'endpoint_url = {endpoint}\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            config_path):
                self.assertEqual(oci_s3.get_endpoint(), endpoint)
        finally:
            os.unlink(config_path)

    def test_get_region_from_config_file(self):
        config_path = self._write_temp_file('[profile oci]\n'
                                            'endpoint_url = https://e\n'
                                            'region = us-sanjose-1\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            config_path):
                self.assertEqual(oci_s3.get_region(), 'us-sanjose-1')
        finally:
            os.unlink(config_path)

    def test_get_region_none_when_unset(self):
        config_path = self._write_temp_file('[profile oci]\n'
                                            'endpoint_url = https://e\n')
        try:
            with mock.patch('sky.adaptors.oci_s3.OCI_S3_CONFIG_PATH',
                            config_path):
                self.assertIsNone(oci_s3.get_region())
        finally:
            os.unlink(config_path)

    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_config')
    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_cred')
    def test_check_storage_credentials_success(self, mock_cred, mock_config):
        mock_cred.return_value = True
        mock_config.return_value = True

        result, hints = oci_s3.check_storage_credentials()

        self.assertTrue(result)
        self.assertIsNone(hints)

    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_config')
    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_cred')
    def test_check_storage_credentials_missing_cred(self, mock_cred,
                                                    mock_config):
        mock_cred.return_value = False
        mock_config.return_value = True

        result, hints = oci_s3.check_storage_credentials()

        self.assertFalse(result)
        self.assertIsNotNone(hints)
        self.assertIn(oci_s3.OCI_S3_CREDENTIALS_PATH, hints)

    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_config')
    @mock.patch('sky.adaptors.oci_s3.oci_s3_profile_in_cred')
    def test_check_storage_credentials_missing_both(self, mock_cred,
                                                    mock_config):
        mock_cred.return_value = False
        mock_config.return_value = False

        result, hints = oci_s3.check_storage_credentials()

        self.assertFalse(result)
        self.assertIsNotNone(hints)
        self.assertIn(oci_s3.OCI_S3_CREDENTIALS_PATH, hints)
        self.assertIn(oci_s3.OCI_S3_CONFIG_PATH, hints)

    def test_check_credentials_unsupported_capability(self):
        with self.assertRaises(exceptions.NotSupportedError):
            oci_s3.check_credentials(cloud.CloudCapability.COMPUTE)

    def test_get_credential_file_mounts(self):
        mounts = oci_s3.get_credential_file_mounts()

        self.assertEqual(
            mounts, {
                oci_s3.OCI_S3_CREDENTIALS_PATH: oci_s3.OCI_S3_CREDENTIALS_PATH,
                oci_s3.OCI_S3_CONFIG_PATH: oci_s3.OCI_S3_CONFIG_PATH,
            })

    @mock.patch('sky.adaptors.oci_s3.session')
    @mock.patch('sky.adaptors.oci_s3.get_oci_s3_credentials')
    @mock.patch('sky.adaptors.oci_s3.get_endpoint')
    @mock.patch('sky.adaptors.oci_s3.get_region')
    def test_client_creation(self, mock_get_region, mock_get_endpoint,
                             mock_get_creds, mock_session):
        mock_session_obj = mock.Mock()
        mock_session.return_value = mock_session_obj

        mock_creds = mock.Mock()
        mock_creds.access_key = 'test_access_key'
        mock_creds.secret_key = 'test_secret_key'
        mock_get_creds.return_value = mock_creds

        mock_get_endpoint.return_value = 'https://test-endpoint.com'
        mock_get_region.return_value = 'us-sanjose-1'

        oci_s3.client('s3')

        mock_session_obj.client.assert_called_once()
        call_args = mock_session_obj.client.call_args
        self.assertEqual(call_args[0][0], 's3')
        self.assertEqual(call_args[1]['endpoint_url'],
                         'https://test-endpoint.com')
        self.assertEqual(call_args[1]['aws_access_key_id'], 'test_access_key')
        self.assertEqual(call_args[1]['aws_secret_access_key'],
                         'test_secret_key')
        self.assertEqual(call_args[1]['region_name'], 'us-sanjose-1')


if __name__ == '__main__':
    unittest.main()
