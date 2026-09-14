"""The ssh WebSocket client's message for a rejected handshake.

`sky/templates/websocket_proxy.py` prints an explanation when the server
refuses the handshake. With `websocket_aware` answering through the
`websocket.http.response` extension, the status the client sees is the
middleware's real one (503 with the server's detail) or the 403 every
older release maps to the login hint; older servers still close the
connection, which arrives as a bare 403 and must keep printing the hint.
"""
import importlib.util
import json
import pathlib
import types

import pytest

from sky import exceptions

_TEMPLATE = pathlib.Path(__file__).parents[4] / 'sky' / 'templates' / (
    'websocket_proxy.py')


def _load_client():
    spec = importlib.util.spec_from_file_location('websocket_proxy_under_test',
                                                  _TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name='client_module')
def _client_module():
    return _load_client()


def _invalid_status(status_code: int,
                    body: bytes = b'',
                    reason: str = '') -> Exception:
    """A stand-in for websockets.exceptions.InvalidStatus."""
    response = types.SimpleNamespace(status_code=status_code,
                                     body=body,
                                     reason_phrase=reason)
    return types.SimpleNamespace(response=response)


def test_a_403_prints_the_login_hint(client_module, capsys):
    client_module._print_handshake_rejection(  # pylint: disable=protected-access
        _invalid_status(403), 'http://server')
    err = capsys.readouterr().err
    assert str(exceptions.ApiServerAuthenticationError('http://server')) in err


def test_a_401_is_read_the_same_way(client_module, capsys):
    """Newer servers send 401 for an auth refusal; the hint is what the
    message must say either way."""
    client_module._print_handshake_rejection(  # pylint: disable=protected-access
        _invalid_status(401), 'http://server')
    err = capsys.readouterr().err
    assert 'sky api login' in err


def test_a_503_prints_the_servers_explanation_not_the_login_hint(
        client_module, capsys):
    detail = 'The server has exhausted its concurrent worker limit.'
    client_module._print_handshake_rejection(  # pylint: disable=protected-access
        _invalid_status(503, body=json.dumps({
            'detail': detail
        }).encode()), 'http://server')
    err = capsys.readouterr().err
    assert detail in err
    # The everyday auth problem this is not: no login hint on a 503.
    assert 'sky api login' not in err


def test_a_503_without_a_body_says_something_readable(client_module, capsys):
    client_module._print_handshake_rejection(  # pylint: disable=protected-access
        _invalid_status(503, reason='Service Unavailable'), 'http://server')
    err = capsys.readouterr().err
    assert 'Service Unavailable' in err


def test_anything_else_prints_the_error(client_module, capsys):
    client_module._print_handshake_rejection(  # pylint: disable=protected-access
        _invalid_status(500), 'http://server')
    err = capsys.readouterr().err
    assert 'Error ssh into cluster' in err
