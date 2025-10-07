"""Tests the functions in authentication.py.

"""

import os
from unittest.mock import patch

from google.auth import exceptions as google_exceptions
import pytest

from sky import authentication as auth
from sky import exceptions


def test_setup_gcp_authentication():
    # Create a mock config with required fields
    mock_config = {
        'provider': {
            'project_id': 'test-project'
        },
        'gcp_credentials': {
            'type': 'service_account',
            'credentials': '{"type": "service_account", "project_id": "test-project"}'
        }
    }

    with patch('sky.adaptors.gcp.build') as mock_build:
        # Mock the compute API response to raise RefreshError
        mock_build.return_value.projects.return_value.get.return_value.execute.side_effect = (
            google_exceptions.RefreshError('test'))

        with pytest.raises(exceptions.InvalidCloudCredentials):
            auth.setup_gcp_authentication(mock_config)


def test_gcp_project_metadata_parsing_normal():
    """Test normal GCP project metadata parsing."""
    # Normal project response with OS Login enabled
    normal_project = {
        'commonInstanceMetadata': {
            'items': [{
                'key': 'enable-oslogin',
                'value': 'True'
            }, {
                'key': 'other-key',
                'value': 'other-value'
            }]
        }
    }

    # Should return 'True' without raising any exceptions
    result = auth.parse_gcp_project_oslogin(normal_project)
    assert result == 'True'


def test_gcp_project_metadata_parsing_no_oslogin():
    """Test GCP project metadata parsing with no OS Login setting."""
    # Project response without OS Login setting
    project_no_oslogin = {
        'commonInstanceMetadata': {
            'items': [{
                'key': 'other-key',
                'value': 'other-value'
            }]
        }
    }

    # Should return 'False' (default) without raising any exceptions
    result = auth.parse_gcp_project_oslogin(project_no_oslogin)
    assert result == 'False'


def test_gcp_project_metadata_parsing_malformed():
    """Test GCP project metadata parsing with malformed responses."""
    malformed_cases = [
        # Missing commonInstanceMetadata
        {},
        # commonInstanceMetadata is not a dict
        {
            'commonInstanceMetadata': 'invalid'
        },
        # Missing items
        {
            'commonInstanceMetadata': {}
        },
        # items is not a list (the original bug case)
        {
            'commonInstanceMetadata': {
                'items': 1
            }
        },
        # items is a string
        {
            'commonInstanceMetadata': {
                'items': 'invalid'
            }
        },
        # items contains invalid entries
        {
            'commonInstanceMetadata': {
                'items': [{
                    'invalid': 'entry'
                }]
            }
        },
    ]

    for malformed_project in malformed_cases:
        # Should not raise any exceptions despite malformed data
        # and should return 'False' (default)
        result = auth.parse_gcp_project_oslogin(malformed_project)
        assert result == 'False'


def test_create_ssh_key_files_from_db():
    """Test create_ssh_key_files_from_db with private_key_path."""
    current_user_hash = 'current_user_hash_789'
    path_user_hash = 'different_user_hash_abc'
    mock_private_key = 'mock_private_key_content'
    mock_public_key = 'mock_public_key_content'
    # Use expanded path as input to match what the function expects
    private_key_path = os.path.expanduser(
        f'~/.sky/clients/{path_user_hash}/ssh/sky-key')

    with patch('sky.authentication.global_user_state.get_ssh_keys') as mock_get_ssh_keys, \
         patch('sky.authentication.os.path.exists') as mock_exists, \
         patch('sky.authentication.filelock.FileLock') as mock_filelock, \
         patch('sky.authentication._save_key_pair') as mock_save_key_pair, \
         patch('sky.authentication.os.makedirs') as mock_makedirs:
        # Setup mocks
        mock_get_ssh_keys.return_value = (mock_public_key, mock_private_key,
                                          True)
        # Mock os.path.exists to return False for private key, True for public key
        mock_exists.side_effect = lambda path: 'sky-key.pub' in path
        mock_filelock.return_value.__enter__ = lambda x: None
        mock_filelock.return_value.__exit__ = lambda x, y, z, w: None

        # Call the function
        auth.create_ssh_key_files_from_db(private_key_path=private_key_path)

        # Verify _save_key_pair was called with the user_hash from the path.
        mock_save_key_pair.assert_called_once()
        args, kwargs = mock_save_key_pair.call_args
        assert args[
            0] == path_user_hash, f"Expected user_hash {path_user_hash}, got {args[0]}"
        assert args[
            0] != current_user_hash, f"Should not use current user hash {current_user_hash}"
