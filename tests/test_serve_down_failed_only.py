"""Tests for sky serve down --failed-only functionality.

This tests the server-side implementation where the controller queries
its own database for failed replicas and terminates them.
"""
import unittest.mock as mock

import click
from click import testing as cli_testing
import pytest

from sky.client.cli import command


class TestServeDownFailedOnlyClient:
    """Test the client-side CLI for --failed-only flag."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        """Set up mocks for testing."""
        # Mock the serve_lib.terminate_failed_replicas to return a request ID
        self.mock_terminate_failed_replicas = mock.MagicMock(
            return_value='request-id-123')

        monkeypatch.setattr(
            'sky.client.cli.command.serve_lib.terminate_failed_replicas',
            self.mock_terminate_failed_replicas)

        # Mock serve_lib.down
        self.mock_down = mock.MagicMock(return_value='request-id-down')
        monkeypatch.setattr('sky.client.cli.command.serve_lib.down',
                            self.mock_down)

        # Mock serve_lib.terminate_replica
        self.mock_terminate_replica = mock.MagicMock(
            return_value='request-id-replica')
        monkeypatch.setattr(
            'sky.client.cli.command.serve_lib.terminate_replica',
            self.mock_terminate_replica)

        # Mock _async_call_or_wait to just return immediately
        def mock_async_wait(*args, **kwargs):
            pass

        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_wait)

    def test_failed_only_calls_terminate_failed_replicas(self):
        """Test that --failed-only calls the correct SDK function."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command succeeded
        assert result.exit_code == 0, (result.exception, result.output)

        # Check that terminate_failed_replicas was called with the service name
        self.mock_terminate_failed_replicas.assert_called_once_with(
            'test-service')

        # Check that regular down was NOT called
        self.mock_down.assert_not_called()

        # Check that terminate_replica was NOT called
        self.mock_terminate_replica.assert_not_called()

    def test_failed_only_requires_single_service(self):
        """Test that --failed-only requires exactly one service name."""
        cli_runner = cli_testing.CliRunner()

        # Test with no service name
        result = cli_runner.invoke(command.serve_down, ['--failed-only', '-y'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'Can only specify one of SERVICE_NAMES or --all' in result.output

        # Test with multiple service names
        result = cli_runner.invoke(
            command.serve_down, ['service1', 'service2', '--failed-only', '-y'])
        assert result.exit_code == click.UsageError.exit_code
        assert 'The --failed-only option can only be used with a single service name' in result.output

    def test_failed_only_cannot_be_used_with_all(self):
        """Test that --failed-only cannot be used with --all."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['--failed-only', '--all', '-y'])

        assert result.exit_code == click.UsageError.exit_code
        # Accepts any of the relevant error messages
        assert ('single service name' in result.output or
                'cannot be used with the --all option' in result.output or
                'Can only specify one of SERVICE_NAMES or --all'
                in result.output)

    def test_failed_only_cannot_be_used_with_replica_id(self):
        """Test that --failed-only cannot be used with --replica-id."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(
            command.serve_down,
            ['test-service', '--failed-only', '--replica-id', '1', '-y'])

        assert result.exit_code == click.UsageError.exit_code
        assert 'The --failed-only option cannot be used with the --replica-id option' in result.output

    def test_failed_only_confirmation_prompt(self):
        """Test that --failed-only shows a confirmation prompt without -y."""
        cli_runner = cli_testing.CliRunner()

        # Test with 'n' (abort)
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='n\n')
        assert result.exit_code == 1  # Aborted
        assert 'Terminating all failed replicas' in result.output
        assert 'purge' in result.output.lower()

        # Verify no actual termination was called
        self.mock_terminate_failed_replicas.assert_not_called()

        # Test with 'y' (proceed)
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='y\n')
        assert result.exit_code == 0
        self.mock_terminate_failed_replicas.assert_called_once()

    def test_regular_down_still_works(self):
        """Test that regular serve down without --failed-only still works."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down, ['test-service', '-y'])

        assert result.exit_code == 0
        self.mock_down.assert_called_once()
        self.mock_terminate_failed_replicas.assert_not_called()

    def test_replica_id_down_still_works(self):
        """Test that serve down with --replica-id still works."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--replica-id', '1', '-y'])

        assert result.exit_code == 0
        self.mock_terminate_replica.assert_called_once_with(
            'test-service', 1, False)
        self.mock_terminate_failed_replicas.assert_not_called()
        self.mock_down.assert_not_called()


class TestServeDownFailedOnlyServerSide:
    """Test the server-side implementation in serve_utils."""

    def test_terminate_failed_replicas_function_exists(self):
        """Test that the terminate_failed_replicas function exists in serve_utils."""
        from sky.serve import serve_utils
        assert hasattr(serve_utils, 'terminate_failed_replicas')
        assert callable(serve_utils.terminate_failed_replicas)

    def test_terminate_failed_replicas_with_no_replicas(self, monkeypatch):
        """Test terminate_failed_replicas when there are no failed replicas."""
        from sky.serve import serve_state
        from sky.serve import serve_utils

        # Mock _get_service_status to return a valid service
        mock_service_status = {
            'name': 'test-service',
            'status': serve_state.ServiceStatus.READY,
            'controller_port': 30001,
            'pool': False,
        }
        monkeypatch.setattr('sky.serve.serve_utils._get_service_status',
                            lambda *args, **kwargs: mock_service_status)

        # Mock get_replicas_at_status to return empty list
        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            lambda *args: [])

        # Mock failed_statuses
        monkeypatch.setattr(
            'sky.serve.serve_state.ReplicaStatus.failed_statuses',
            lambda: [serve_state.ReplicaStatus.FAILED_PROVISION])

        result = serve_utils.terminate_failed_replicas('test-service')
        assert 'No failed replicas found' in result

    def test_terminate_failed_replicas_with_service_not_found(
            self, monkeypatch):
        """Test terminate_failed_replicas when service doesn't exist."""
        from sky.serve import serve_utils

        # Mock _get_service_status to return None
        monkeypatch.setattr('sky.serve.serve_utils._get_service_status',
                            lambda *args, **kwargs: None)

        with pytest.raises(ValueError, match="does not exist"):
            serve_utils.terminate_failed_replicas('nonexistent-service')

    def test_codegen_terminate_failed_replicas(self):
        """Test that ServeCodeGen has the terminate_failed_replicas method."""
        from sky.serve.serve_utils import ServeCodeGen

        assert hasattr(ServeCodeGen, 'terminate_failed_replicas')
        code = ServeCodeGen.terminate_failed_replicas('test-service')

        # Check that the generated code contains the function call
        assert 'serve_utils.terminate_failed_replicas' in code
        assert "'test-service'" in code or '"test-service"' in code


class TestServeDownFailedOnlyIntegration:
    """Integration tests for the complete flow."""

    def test_help_text_mentions_failed_only(self):
        """Test that the help text for serve down mentions --failed-only."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down, ['--help'])

        assert result.exit_code == 0
        assert '--failed-only' in result.output
        # Check that it mentions purge behavior
        assert 'purge' in result.output.lower(
        ) or 'forcefully' in result.output.lower()

    def test_sdk_function_exists(self):
        """Test that the SDK function exists."""
        from sky import serve
        assert hasattr(serve, 'terminate_failed_replicas')
        assert callable(serve.terminate_failed_replicas)

    def test_server_core_function_exists(self):
        """Test that the server core function exists."""
        from sky.serve.server import core
        assert hasattr(core, 'terminate_failed_replicas')
        assert callable(core.terminate_failed_replicas)

    def test_payload_class_exists(self):
        """Test that the payload class exists."""
        from sky.server.requests import payloads
        assert hasattr(payloads, 'ServeTerminateFailedReplicasBody')

        # Test instantiation
        body = payloads.ServeTerminateFailedReplicasBody(service_name='test')
        assert body.service_name == 'test'


class TestConsistency:
    """Test consistency and error handling."""

    def test_error_messages_are_clear(self):
        """Test that error messages are user-friendly."""
        cli_runner = cli_testing.CliRunner()

        # Test multiple service names error
        result = cli_runner.invoke(
            command.serve_down, ['service1', 'service2', '--failed-only', '-y'])
        assert 'single service name' in result.output

        # Test with --all error
        result = cli_runner.invoke(command.serve_down,
                                   ['--failed-only', '--all', '-y'])
        assert result.exit_code != 0

        # Test with --replica-id error
        result = cli_runner.invoke(
            command.serve_down,
            ['test-service', '--failed-only', '--replica-id', '1', '-y'])
        assert 'cannot be used with the --replica-id option' in result.output

    def test_confirmation_message_mentions_purge(self):
        """Test that confirmation mentions purge."""
        cli_runner = cli_testing.CliRunner()

        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='n\n')
        # Check confirmation mentions purge
        assert 'purge' in result.output.lower()
