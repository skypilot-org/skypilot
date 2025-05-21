"""Tests the functions in authentication.py.

"""

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
