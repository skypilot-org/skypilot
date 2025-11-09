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
