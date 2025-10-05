"""Tests for sky serve down --failed-only functionality."""
import unittest.mock as mock

import click
from click import testing as cli_testing
import pytest

from sky.client.cli import command
from sky.serve import serve_state


class TestServeDownFailedOnly:
    """Test the --failed-only flag for sky serve down."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        """Set up mocks for testing."""
        # Mock the serve_lib module
        self.mock_terminate_replica = mock.MagicMock(return_value='request-id')
        self.mock_down = mock.MagicMock()

        monkeypatch.setattr(
            'sky.client.cli.command.serve_lib.terminate_replica',
            self.mock_terminate_replica)
        monkeypatch.setattr('sky.client.cli.command.serve_lib.down',
                            self.mock_down)

        # Mock _async_call_or_wait to just return immediately
        def mock_async_wait(*args, **kwargs):
            pass

        monkeypatch.setattr('sky.client.cli.command._async_call_or_wait',
                            mock_async_wait)

    def test_failed_only_with_failed_replicas(self, monkeypatch):
        """Test --failed-only when there are failed replicas."""
        # Create mock replica objects
        mock_replica1 = mock.MagicMock()
        mock_replica1.replica_id = 1
        mock_replica1.status = serve_state.ReplicaStatus.FAILED_PROVISION

        mock_replica2 = mock.MagicMock()
        mock_replica2.replica_id = 3
        mock_replica2.status = serve_state.ReplicaStatus.FAILED_PROBING

        # Mock get_replicas_at_status to return different replicas for different statuses
        def mock_get_replicas_at_status(service_name, status):
            if status == serve_state.ReplicaStatus.FAILED_PROVISION:
                return [mock_replica1]
            elif status == serve_state.ReplicaStatus.FAILED_PROBING:
                return [mock_replica2]
            else:
                return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command succeeded
        assert result.exit_code == 0, (result.exception, result.output)

        # Check that terminate_replica was called for each failed replica
        assert self.mock_terminate_replica.call_count == 2

        # Verify the calls were made with correct parameters
        # Check that both replica IDs were called with purge=True
        replica_ids_called = set()
        for call in self.mock_terminate_replica.call_args_list:
            args, kwargs = call
            assert args[0] == 'test-service'
            replica_ids_called.add(args[1])
            # purge might be in args[2] or kwargs['purge']
            if len(args) > 2:
                assert args[2] == True  # purge=True
            else:
                assert kwargs.get('purge', False) == True

        assert replica_ids_called == {1, 3}

        # Check output mentions the replicas
        assert 'Found 2 failed replica(s)' in result.output
        assert 'Successfully terminated 2 failed replica(s)' in result.output

    def test_failed_only_with_no_failed_replicas(self, monkeypatch):
        """Test --failed-only when there are no failed replicas."""

        # Mock get_replicas_at_status to return empty list
        def mock_get_replicas_at_status(service_name, status):
            return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command succeeded
        assert result.exit_code == 0, (result.exception, result.output)

        # Check that terminate_replica was NOT called
        assert self.mock_terminate_replica.call_count == 0

        # Check output mentions no failed replicas
        assert 'No failed replicas found' in result.output

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
        # The validation for --failed-only requiring a single service happens first
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

    def test_failed_only_confirmation_prompt(self, monkeypatch):
        """Test that --failed-only shows a confirmation prompt without -y."""
        # Mock get_replicas_at_status to return a failed replica
        mock_replica = mock.MagicMock()
        mock_replica.replica_id = 1
        mock_replica.status = serve_state.ReplicaStatus.FAILED_PROVISION

        def mock_get_replicas_at_status(service_name, status):
            if status == serve_state.ReplicaStatus.FAILED_PROVISION:
                return [mock_replica]
            return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        cli_runner = cli_testing.CliRunner()

        # Test with 'n' (abort)
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='n\n')
        assert result.exit_code == 1  # Aborted
        assert 'Terminating all failed replicas' in result.output

        # Test with 'y' (proceed)
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='y\n')
        assert result.exit_code == 0
        assert self.mock_terminate_replica.call_count == 1

    def test_failed_only_deduplicates_replica_ids(self, monkeypatch):
        """Test that --failed-only deduplicates replicas with same ID."""
        # Create mock replicas with the same ID appearing in multiple statuses
        mock_replica1_v1 = mock.MagicMock()
        mock_replica1_v1.replica_id = 1
        mock_replica1_v1.status = serve_state.ReplicaStatus.FAILED_PROVISION

        mock_replica1_v2 = mock.MagicMock()
        mock_replica1_v2.replica_id = 1
        mock_replica1_v2.status = serve_state.ReplicaStatus.FAILED_PROBING

        mock_replica2 = mock.MagicMock()
        mock_replica2.replica_id = 2
        mock_replica2.status = serve_state.ReplicaStatus.FAILED_CLEANUP

        # Mock get_replicas_at_status to return overlapping replica IDs
        def mock_get_replicas_at_status(service_name, status):
            if status == serve_state.ReplicaStatus.FAILED_PROVISION:
                return [mock_replica1_v1]
            elif status == serve_state.ReplicaStatus.FAILED_PROBING:
                return [mock_replica1_v2]  # Same replica ID as above
            elif status == serve_state.ReplicaStatus.FAILED_CLEANUP:
                return [mock_replica2]
            else:
                return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command succeeded
        assert result.exit_code == 0, (result.exception, result.output)

        # Verify terminate_replica was called only 2 times (not 3)
        # because replica ID 1 should be deduplicated
        assert self.mock_terminate_replica.call_count == 2

        # Verify the calls were made for replica IDs 1 and 2
        replica_ids_called = set()
        for call in self.mock_terminate_replica.call_args_list:
            args, kwargs = call
            replica_ids_called.add(args[1])

        assert replica_ids_called == {1, 2}

        # Check output mentions 2 replicas (deduplicated)
        assert 'Found 2 failed replica(s)' in result.output

    def test_failed_only_handles_termination_errors(self, monkeypatch):
        """Test that --failed-only continues when individual terminations fail."""
        # Create mock replicas
        mock_replica1 = mock.MagicMock()
        mock_replica1.replica_id = 1
        mock_replica1.status = serve_state.ReplicaStatus.FAILED_PROVISION

        mock_replica2 = mock.MagicMock()
        mock_replica2.replica_id = 2
        mock_replica2.status = serve_state.ReplicaStatus.FAILED_PROBING

        mock_replica3 = mock.MagicMock()
        mock_replica3.replica_id = 3
        mock_replica3.status = serve_state.ReplicaStatus.FAILED_CLEANUP

        def mock_get_replicas_at_status(service_name, status):
            if status == serve_state.ReplicaStatus.FAILED_PROVISION:
                return [mock_replica1]
            elif status == serve_state.ReplicaStatus.FAILED_PROBING:
                return [mock_replica2]
            elif status == serve_state.ReplicaStatus.FAILED_CLEANUP:
                return [mock_replica3]
            else:
                return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        # Make terminate_replica fail for replica 2
        def mock_terminate_with_error(service_name, replica_id, purge):
            if replica_id == 2:
                raise RuntimeError('Simulated termination error')
            return 'request-id'

        self.mock_terminate_replica.side_effect = mock_terminate_with_error

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command succeeded (doesn't abort on error)
        assert result.exit_code == 0, (result.exception, result.output)

        # Verify terminate_replica was called 3 times (tried all)
        assert self.mock_terminate_replica.call_count == 3

        # Check output mentions both success and failure
        assert 'Successfully terminated 2 failed replica(s)' in result.output
        assert '1 replica(s) could not be terminated' in result.output
        assert 'Failed to terminate replica 2' in result.output

    def test_failed_only_handles_get_replicas_error(self, monkeypatch):
        """Test that --failed-only handles errors from get_replicas_at_status."""

        def mock_get_replicas_error(service_name, status):
            raise RuntimeError('Simulated get_replicas error')

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_error)

        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only', '-y'])

        # Check that the command failed
        assert result.exit_code != 0

        # Check output mentions the error
        assert 'Error retrieving failed replicas' in result.output

    def test_failed_only_help_text_mentions_purge(self):
        """Test that the help text for --failed-only mentions purge."""
        cli_runner = cli_testing.CliRunner()
        result = cli_runner.invoke(command.serve_down, ['--help'])

        assert result.exit_code == 0
        # Check that the help text mentions purge behavior
        assert '--failed-only' in result.output
        assert 'purge' in result.output.lower(
        ) or 'forcefully' in result.output.lower()

    def test_failed_only_confirmation_mentions_purge(self, monkeypatch):
        """Test that the confirmation prompt mentions purge."""
        # Mock get_replicas_at_status to return a failed replica
        mock_replica = mock.MagicMock()
        mock_replica.replica_id = 1
        mock_replica.status = serve_state.ReplicaStatus.FAILED_PROVISION

        def mock_get_replicas_at_status(service_name, status):
            if status == serve_state.ReplicaStatus.FAILED_PROVISION:
                return [mock_replica]
            return []

        monkeypatch.setattr('sky.serve.serve_state.get_replicas_at_status',
                            mock_get_replicas_at_status)

        cli_runner = cli_testing.CliRunner()

        # Test confirmation prompt
        result = cli_runner.invoke(command.serve_down,
                                   ['test-service', '--failed-only'],
                                   input='n\n')
        assert result.exit_code == 1  # Aborted
        # Check that confirmation mentions purge
        assert 'purge' in result.output.lower()
