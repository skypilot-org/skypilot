from unittest import mock

import pytest

from sky.provision import provisioner


class _DummySocket:
    """Simple context manager socket mock for SSH probing tests."""

    def __init__(self, recv_payload: bytes = b'SSH'):
        self._recv_payload = recv_payload

    def recv(self, _num_bytes: int) -> bytes:
        return self._recv_payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _setup_successful_socket(monkeypatch):
    dummy_socket = _DummySocket()
    create_connection_mock = mock.MagicMock(return_value=dummy_socket)
    monkeypatch.setattr(provisioner.socket,
                        'create_connection',
                        create_connection_mock)
    monkeypatch.setattr(provisioner,
                        '_wait_ssh_connection_indirect',
                        mock.MagicMock(return_value=(True, '')))
    return create_connection_mock


def test_wait_ssh_connection_direct_uses_default_timeout(monkeypatch):
    monkeypatch.delenv('SKYPILOT_SSH_SOCKET_CONNECT_TIMEOUT', raising=False)
    create_connection_mock = _setup_successful_socket(monkeypatch)

    success, stderr = provisioner._wait_ssh_connection_direct(
        '1.2.3.4',
        22,
        ssh_user='user',
        ssh_private_key='key',
        ssh_probe_timeout=5)

    assert success
    assert stderr == ''
    assert create_connection_mock.call_args.kwargs['timeout'] == 1.0


def test_wait_ssh_connection_direct_env_override(monkeypatch):
    monkeypatch.setenv('SKYPILOT_SSH_SOCKET_CONNECT_TIMEOUT', '2.5')
    create_connection_mock = _setup_successful_socket(monkeypatch)

    success, _ = provisioner._wait_ssh_connection_direct(
        '5.6.7.8',
        22,
        ssh_user='user',
        ssh_private_key='key',
        ssh_probe_timeout=5)

    assert success
    assert create_connection_mock.call_args.kwargs['timeout'] == pytest.approx(
        2.5)


@pytest.mark.parametrize('value', ['abc', '0', '-1'])
def test_wait_ssh_connection_direct_invalid_env(monkeypatch, value):
    monkeypatch.setenv('SKYPILOT_SSH_SOCKET_CONNECT_TIMEOUT', value)
    create_connection_mock = mock.MagicMock()
    monkeypatch.setattr(provisioner.socket,
                        'create_connection',
                        create_connection_mock)
    monkeypatch.setattr(provisioner,
                        '_wait_ssh_connection_indirect',
                        mock.MagicMock(return_value=(True, '')))

    success, stderr = provisioner._wait_ssh_connection_direct(
        '9.9.9.9',
        22,
        ssh_user='user',
        ssh_private_key='key',
        ssh_probe_timeout=5)

    assert not success
    assert 'Invalid SKYPILOT_SSH_SOCKET_CONNECT_TIMEOUT' in stderr
    create_connection_mock.assert_not_called()
