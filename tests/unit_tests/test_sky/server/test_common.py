"""Unit tests for sky.server.common module."""
from unittest import mock

import pytest

from sky import sky_logging
from sky import skypilot_config
from sky.server import common
from sky.usage import usage_lib
from sky.utils import common_utils


@pytest.fixture
def mock_all_dependencies():
    """Mock all dependencies used in reload_for_new_request."""
    with mock.patch('sky.utils.common_utils.set_client_status') as mock_status, \
         mock.patch('sky.usage.usage_lib.messages.reset') as mock_reset, \
         mock.patch('sky.sky_logging.reload_logger') as mock_logger:
        yield {
            'set_status': mock_status,
            'reset_messages': mock_reset,
            'reload_logger': mock_logger
        }


def test_reload_config_for_new_request(mock_all_dependencies, tmp_path,
                                       monkeypatch):
    """Test basic functionality with all parameters provided."""
    config_path = tmp_path / 'config.yaml'
    config_path.write_text('''
allowed_clouds:
  - aws
''')

    # Set env var to point to the temp config
    monkeypatch.setenv('SKYPILOT_CONFIG', str(config_path))
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['aws']
    config_path.write_text('''
allowed_clouds:
  - gcp
''')
    common.reload_for_new_request(
        client_entrypoint='test_entry',
        client_command='test_cmd',
        using_remote_api_server=False,
    )
    assert skypilot_config.get_nested(keys=('allowed_clouds',),
                                      default_value=None) == ['gcp']
