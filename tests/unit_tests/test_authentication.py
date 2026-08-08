"""Tests the functions in authentication.py.

"""

import os
from unittest.mock import MagicMock
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


def test_runpod_key_label_with_username():
    with patch('sky.utils.common_utils.get_user_hash',
               return_value='abcdef1234567890'), \
         patch('sky.utils.common_utils.get_cleaned_username',
               return_value='alice'):
        assert auth._runpod_key_label() == 'skypilot-alice-abcdef12'


def test_runpod_key_label_falls_back_when_username_unresolved():
    with patch('sky.utils.common_utils.get_user_hash',
               return_value='abcdef1234567890'), \
         patch('sky.utils.common_utils.get_cleaned_username',
               side_effect=RuntimeError('no current user')):
        assert auth._runpod_key_label() == 'skypilot-abcdef12'


def test_runpod_key_label_falls_back_when_username_empty():
    with patch('sky.utils.common_utils.get_user_hash',
               return_value='abcdef1234567890'), \
         patch('sky.utils.common_utils.get_cleaned_username', return_value=''):
        assert auth._runpod_key_label() == 'skypilot-abcdef12'


def _register_runpod_key(tmp_path, key_content):
    """Runs setup_runpod_authentication and returns the key string that would
    be registered with RunPod."""
    pub_key_path = tmp_path / 'sky-key.pub'
    pub_key_path.write_text(key_content)
    mock_runpod = MagicMock()
    with patch('sky.authentication.auth_utils.get_or_generate_keys',
               return_value=('priv', str(pub_key_path))), \
         patch('sky.authentication.runpod', mock_runpod), \
         patch('sky.authentication.configure_ssh_info',
               side_effect=lambda config: config), \
         patch('sky.utils.common_utils.get_user_hash',
               return_value='abcdef1234567890'), \
         patch('sky.utils.common_utils.get_cleaned_username',
               return_value='alice'):
        auth.setup_runpod_authentication({'auth': {}})
    add_ssh_key = mock_runpod.runpod.cli.groups.ssh.functions.add_ssh_key
    add_ssh_key.assert_called_once()
    return add_ssh_key.call_args[0][0]


def test_setup_runpod_authentication_labels_bare_key(tmp_path):
    registered = _register_runpod_key(tmp_path, 'ssh-rsa AAAAB3NzaC1yc2E\n')
    parts = registered.split(' ')
    assert parts[:2] == ['ssh-rsa', 'AAAAB3NzaC1yc2E']  # material untouched
    assert parts[2] == 'skypilot-alice-abcdef12'
    assert len(parts) == 3  # single-token comment for RunPod's parser


def test_setup_runpod_authentication_replaces_existing_comment(tmp_path):
    registered = _register_runpod_key(
        tmp_path, 'ssh-rsa AAAAB3NzaC1yc2E old@host trailing words\n')
    parts = registered.split(' ')
    assert parts[:2] == ['ssh-rsa', 'AAAAB3NzaC1yc2E']
    assert parts[2] == 'skypilot-alice-abcdef12'
    assert len(parts) == 3
