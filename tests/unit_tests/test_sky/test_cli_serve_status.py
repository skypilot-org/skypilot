"""Tests for the full replica output flags of ``sky serve status``."""
from unittest import mock

from click.testing import CliRunner
import pytest

from sky.client.cli import command
from sky.serve import serve_state


@pytest.fixture(name='serve_request')
def mock_serve_request():
    record = {
        'name': 'my-service',
        'active_versions': [1],
        'uptime': 0,
        'status': serve_state.ServiceStatus.READY,
        'endpoint': 'http://service:8080',
        'policy': 'fixed',
        'requested_resources_str': 'CPU:1',
        'load_balancing_policy': 'round_robin',
        'replica_info': [{
            'replica_id': replica_id,
            'version': 1,
            'endpoint': f'http://replica-{replica_id}:8080',
            'launched_at': 0,
            'status': serve_state.ReplicaStatus.READY,
            'infra': 'mock',
            'resources_str': 'CPU:1',
            'resources_str_full': 'CPU:1',
        } for replica_id in range(1, 12)],
    }
    with mock.patch.object(command.serve_lib, 'status',
                           return_value='request-id') as status, \
         mock.patch.object(command.sdk, 'get', return_value=[record]):
        yield status


@pytest.mark.parametrize('args,show_all', [
    ([], False),
    (['--all'], True),
    (['-a'], True),
    (['--verbose'], True),
    (['-v'], True),
    (['--all', '--verbose'], True),
])
def test_replica_output(args, show_all, serve_request):
    result = CliRunner().invoke(command.serve_status, args)
    assert result.exit_code == 0, result.output
    serve_request.assert_called_once_with(None)
    assert ('http://replica-11:8080' in result.output) == show_all
    assert ('use --all' in result.output) != show_all


@pytest.mark.parametrize('flag', ['--all', '-a'])
def test_all_preserves_service_selection(flag, serve_request):
    result = CliRunner().invoke(command.serve_status, ['my-service', flag])
    assert result.exit_code == 0, result.output
    serve_request.assert_called_once_with(('my-service',))
    assert 'http://replica-11:8080' in result.output


def test_all_preserves_endpoint_output(serve_request):
    result = CliRunner().invoke(command.serve_status,
                                ['my-service', '--all', '--endpoint'])
    assert result.exit_code == 0, result.output
    serve_request.assert_called_once_with(('my-service',))
    assert result.output.strip() == 'http://service:8080'


def test_help_documents_all(serve_request):
    result = CliRunner().invoke(command.serve_status, ['--help'])
    assert result.exit_code == 0, result.output
    assert '-a, --all' in result.output
    assert 'Show all replicas.' in result.output
    serve_request.assert_not_called()
