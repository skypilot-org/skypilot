"""Tests for sky api info CLI command.

This module contains tests for the api_info function in
sky.client.cli.command module, including JSON output support.
"""
import json
from unittest import mock

from click.testing import CliRunner
import pytest

import sky
from sky import models
from sky import skypilot_config
from sky.client import sdk
from sky.client.cli import command
from sky.schemas.api import responses
from sky.server import common as server_common


class TestApiInfo:
    """Test suite for the api_info function."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up common mocks for all tests."""
        self.runner = CliRunner()

        # Mock server_common.get_server_url
        self.mock_get_server_url = mock.patch.object(
            server_common,
            'get_server_url',
            return_value='http://localhost:46580')

        # Mock sdk.api_info
        self.mock_api_info = mock.patch.object(sdk, 'api_info')

        # Mock skypilot_config.get_user_config
        self.mock_get_user_config = mock.patch.object(skypilot_config,
                                                      'get_user_config',
                                                      return_value={})

        # Mock skypilot_config.resolve_user_config_path
        self.mock_resolve_user_config_path = mock.patch.object(
            skypilot_config, 'resolve_user_config_path', return_value=None)

        # Mock server_common.check_and_print_upgrade_hint
        self.mock_check_upgrade = mock.patch.object(
            server_common, 'check_and_print_upgrade_hint')

        # Mock skypilot_config.get_active_workspace
        self.mock_get_workspace = mock.patch.object(skypilot_config,
                                                    'get_active_workspace',
                                                    return_value='default')

        self.get_server_url_mock = self.mock_get_server_url.start()
        self.api_info_mock = self.mock_api_info.start()
        self.get_user_config_mock = self.mock_get_user_config.start()
        self.resolve_user_config_path_mock = (
            self.mock_resolve_user_config_path.start())
        self.check_upgrade_mock = self.mock_check_upgrade.start()
        self.get_workspace_mock = self.mock_get_workspace.start()

        # Set up default return value for api_info
        self.api_info_mock.return_value = responses.APIHealthResponse(
            status=server_common.ApiServerStatus.HEALTHY,
            api_version='33',
            version='1.0.0',
            commit='abc1234',
            user=models.User(id='user123', name='alice'),
        )

        yield

        self.mock_get_server_url.stop()
        self.mock_api_info.stop()
        self.mock_get_user_config.stop()
        self.mock_resolve_user_config_path.stop()
        self.mock_check_upgrade.stop()
        self.mock_get_workspace.stop()

    # ==========================================================================
    # Default (table) output tests
    # ==========================================================================

    def test_api_info_default_output(self):
        """Test api_info with default (table/text) output."""
        result = self.runner.invoke(command.api, ['info'])

        assert result.exit_code == 0
        assert 'SkyPilot client version:' in result.output
        assert 'http://localhost:46580' in result.output
        assert 'alice' in result.output
        assert 'healthy' in result.output.lower()

    def test_api_info_explicit_table_output(self):
        """Test api_info with explicit -o table output."""
        result = self.runner.invoke(command.api, ['info', '-o', 'table'])

        assert result.exit_code == 0
        assert 'SkyPilot client version:' in result.output
        assert 'http://localhost:46580' in result.output

    # ==========================================================================
    # JSON output tests
    # ==========================================================================

    def test_api_info_json_output(self):
        """Test api_info with -o json produces valid JSON."""
        result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        # Should be valid JSON
        data = json.loads(result.output)
        assert 'client' in data
        assert 'server' in data
        assert 'user' in data

    def test_api_info_json_output_structure(self):
        """Test api_info JSON output has expected structure."""
        result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        data = json.loads(result.output)

        # Check client section
        assert 'version' in data['client']
        assert 'commit' in data['client']
        assert data['client']['version'] == sky.__version__
        assert data['client']['commit'] == sky.__commit__

        # Check server section
        assert 'url' in data['server']
        assert 'status' in data['server']
        assert 'version' in data['server']
        assert 'commit' in data['server']
        assert 'api_version' in data['server']
        assert data['server']['url'] == 'http://localhost:46580'
        assert data['server']['status'] == 'healthy'
        assert data['server']['version'] == '1.0.0'
        assert data['server']['commit'] == 'abc1234'
        assert data['server']['api_version'] == '33'

        # Check user
        assert data['user'] == 'alice'

    def test_api_info_json_output_long_option(self):
        """Test api_info with --output json."""
        result = self.runner.invoke(command.api, ['info', '--output', 'json'])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert 'client' in data
        assert 'server' in data

    def test_api_info_json_no_spinners(self):
        """Test api_info JSON output doesn't include spinner/status messages."""
        result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        # Output should be pure JSON, no extra messages
        # Try to parse - will fail if there's extra text
        data = json.loads(result.output)
        assert isinstance(data, dict)

    # ==========================================================================
    # Edge case tests
    # ==========================================================================

    def test_api_info_json_user_none(self):
        """Test api_info JSON output when server user is None."""
        self.api_info_mock.return_value = responses.APIHealthResponse(
            status=server_common.ApiServerStatus.HEALTHY,
            api_version='33',
            version='1.0.0',
            commit='abc1234',
            user=None,
        )
        # When user is None, the code falls back to models.User.get_current_user()
        with mock.patch.object(models.User,
                               'get_current_user',
                               return_value=models.User(id='local123',
                                                        name='localuser')):
            result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['user'] == 'localuser'

    def test_api_info_json_server_unhealthy(self):
        """Test api_info JSON output when server is unhealthy."""
        self.api_info_mock.return_value = responses.APIHealthResponse(
            status=server_common.ApiServerStatus.UNHEALTHY,
            api_version='33',
            version='1.0.0',
            commit='abc1234',
            user=models.User(id='user123', name='alice'),
        )

        result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['server']['status'] == 'unhealthy'

    def test_api_info_json_empty_commit(self):
        """Test api_info JSON output with empty commit."""
        self.api_info_mock.return_value = responses.APIHealthResponse(
            status=server_common.ApiServerStatus.HEALTHY,
            api_version='33',
            version='1.0.0',
            commit='',
            user=models.User(id='user123', name='alice'),
        )

        result = self.runner.invoke(command.api, ['info', '-o', 'json'])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['server']['commit'] == ''

    # ==========================================================================
    # Invalid option tests
    # ==========================================================================

    def test_api_info_invalid_output_format(self):
        """Test api_info with invalid output format."""
        result = self.runner.invoke(command.api, ['info', '-o', 'yaml'])

        assert result.exit_code != 0
        assert "Invalid value for '--output'" in result.output

    def test_api_info_invalid_output_format_csv(self):
        """Test api_info with csv output format (not supported)."""
        result = self.runner.invoke(command.api, ['info', '-o', 'csv'])

        assert result.exit_code != 0
        assert "Invalid value for '--output'" in result.output
