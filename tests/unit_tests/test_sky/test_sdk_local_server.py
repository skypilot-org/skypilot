"""Tests for local API server process discovery in the client SDK."""
from sky.client import sdk
from sky.server import common as server_common

_CMD = ['python', '-m', 'sky.server.server']


def test_cmdline_port_equals_form():
    assert sdk._cmdline_api_server_port(_CMD + ['--port=46590']) == 46590


def test_cmdline_port_separate_arg_form():
    assert sdk._cmdline_api_server_port(_CMD + ['--port', '46591']) == 46591


def test_cmdline_port_missing_defaults():
    assert (sdk._cmdline_api_server_port(_CMD + ['--host=127.0.0.1']) ==
            server_common.DEFAULT_SERVER_PORT)


def test_cmdline_port_malformed_defaults():
    assert (sdk._cmdline_api_server_port(_CMD + ['--port=oops']) ==
            server_common.DEFAULT_SERVER_PORT)
