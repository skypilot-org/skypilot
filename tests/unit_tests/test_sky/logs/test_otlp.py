"""Unit tests for sky.logs.otlp module."""

import os
import stat
import subprocess
from unittest import mock

import jsonschema
import pytest

from sky import exceptions
from sky import logs
from sky.logs import otlp
from sky.logs.otlp import _AWK_HEADER_VALUE
from sky.logs.otlp import _REMOTE_HEADERS_PATH
from sky.logs.otlp import OtlpLoggingAgent
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas

_CLUSTER_NAME = resources_utils.ClusterName('test-cluster',
                                            'test-cluster-unique-id')


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Headers files are staged under ~/.sky/logging; keep that out of the
    # real home directory.
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _write_headers_file(tmp_path, content: str) -> str:
    path = tmp_path / 'otlp_headers'
    path.write_text(content, encoding='utf-8')
    return str(path)


def _output(agent: OtlpLoggingAgent):
    return agent.fluentbit_output_config(_CLUSTER_NAME)


@pytest.mark.parametrize('endpoint,expected', [
    ('http://collector:4318', ('collector', 4318, '/v1/logs', 'off')),
    ('https://collector', ('collector', 443, '/v1/logs', 'on')),
    ('http://collector', ('collector', 80, '/v1/logs', 'off')),
    ('https://gw.example.com/otlp/',
     ('gw.example.com', 443, '/otlp/v1/logs', 'on')),
])
def test_endpoint_parsing(endpoint, expected):
    cfg = _output(OtlpLoggingAgent({'endpoint': endpoint}))
    assert (cfg['host'], cfg['port'], cfg['logs_uri'], cfg['tls']) == expected
    assert cfg['name'] == 'opentelemetry'


def test_defaults():
    cfg = _output(OtlpLoggingAgent({'endpoint': 'https://collector:4318'}))
    assert cfg['tls.verify'] == 'on'
    assert 'grpc' not in cfg
    assert 'compress' not in cfg
    assert 'header' not in cfg
    assert cfg['logs_body_key'] == '$log'


def test_protocol_compression_and_tls():
    cfg = _output(
        OtlpLoggingAgent({
            'endpoint': 'https://collector:4317',
            'protocol': 'GRPC',
            'compression': 'gzip',
            'tls': {
                'insecure_skip_verify': True
            },
        }))
    assert cfg['grpc'] == 'on'
    assert cfg['compress'] == 'gzip'
    assert cfg['tls.verify'] == 'off'


@pytest.mark.parametrize('config', [
    {},
    {
        'endpoint': 'collector:4318'
    },
    {
        'endpoint': 'ftp://collector'
    },
    {
        'endpoint': 'http://collector:abc'
    },
    {
        'endpoint': 'http://collector:99999'
    },
    {
        'endpoint': 'http://collector',
        'protocol': 'http/json'
    },
    {
        'endpoint': 'http://collector',
        'compression': 'zstd'
    },
])
def test_invalid_config(config):
    with pytest.raises(exceptions.InvalidSkyPilotConfigError):
        OtlpLoggingAgent(config)


def test_resource_attributes():
    cfg = _output(
        OtlpLoggingAgent({
            'endpoint': 'http://collector:4318',
            'resource_attributes': {
                'deployment.environment': 'prod',
                'service.name': 'my-service',
            },
        }))
    processors = cfg['processors']['logs']
    assert processors[0] == {'name': 'opentelemetry_envelope'}
    attrs = {}
    for p in processors[1:]:
        assert p['context'] == 'otel_resource_attributes'
        attrs[p['key']] = p['value']
    assert attrs == {
        # User attributes can override the default service.name.
        'service.name': 'my-service',
        'skypilot.cluster_name': 'test-cluster',
        'skypilot.cluster_id': 'test-cluster-unique-id',
        'deployment.environment': 'prod',
    }


def test_plain_headers_are_inlined():
    cfg = _output(
        OtlpLoggingAgent({
            'endpoint': 'http://collector:4318',
            'headers': {
                'X-Scope-OrgID': 'tenant-1'
            },
        }))
    assert cfg['header'] == ['X-Scope-OrgID tenant-1']


def _agent_with_headers_file(path: str) -> OtlpLoggingAgent:
    return OtlpLoggingAgent({
        'endpoint': 'http://collector:4318',
        'headers_file': path,
    })


def _staged_path(agent: OtlpLoggingAgent) -> str:
    return agent.get_credential_file_mounts()[_REMOTE_HEADERS_PATH]


def _node_value(path: str, n: int) -> str:
    """Runs the node-side extraction of header n against a delivered file."""
    return subprocess.run(['awk', '-v', f'n={n}', _AWK_HEADER_VALUE, path],
                          check=True,
                          capture_output=True,
                          text=True).stdout


def test_headers_file_values_never_leave_the_file(tmp_path):
    agent = _agent_with_headers_file(
        _write_headers_file(
            tmp_path, '# comment\n\nAuthorization: Bearer s3cret\n'
            'X-Api-Key:k3y\n'))
    cfg = _output(agent)
    assert cfg['header'] == [
        'Authorization ${SKYPILOT_OTLP_HEADER_0}',
        'X-Api-Key ${SKYPILOT_OTLP_HEADER_1}',
    ]
    setup = agent.get_setup_command(_CLUSTER_NAME)
    assert 'export SKYPILOT_OTLP_HEADER_0=' in setup
    assert 'export SKYPILOT_OTLP_HEADER_1=' in setup
    # Neither the setup command (which is logged) nor the fluent-bit config
    # (which is written to the node) contains the secret values.
    for text in (setup, agent.fluentbit_config(_CLUSTER_NAME)):
        assert 's3cret' not in text
        assert 'k3y' not in text


def test_headers_file_crlf_and_blank_lines(tmp_path):
    """Header N on the node is header N on the API server, whatever the
    source file's line endings, blank lines, comments or spacing."""
    path = tmp_path / 'otlp_headers'
    path.write_bytes(b'# comment\r\n\r\n'
                     b'Authorization:   Bearer a b  \r\n'
                     b'\r\n  # indented comment\r\n'
                     b'X-Api-Key:k3y:with:colons\r\n')
    agent = _agent_with_headers_file(str(path))
    assert _output(agent)['header'] == [
        'Authorization ${SKYPILOT_OTLP_HEADER_0}',
        'X-Api-Key ${SKYPILOT_OTLP_HEADER_1}',
    ]
    staged = _staged_path(agent)
    with open(staged, 'rb') as f:
        assert f.read() == (b'Authorization: Bearer a b\n'
                            b'X-Api-Key: k3y:with:colons\n')
    assert _node_value(staged, 1) == 'Bearer a b\n'
    assert _node_value(staged, 2) == 'k3y:with:colons\n'


def test_staged_headers_file_is_owner_only(tmp_path):
    """The upload (rsync -a) keeps the source mode, so the uploaded copy must
    be 0600 even when the user's file is world-readable."""
    path = _write_headers_file(tmp_path, 'Authorization: Bearer x\n')
    os.chmod(path, 0o644)
    staged = _staged_path(_agent_with_headers_file(path))
    assert stat.S_IMODE(os.stat(staged).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(staged)).st_mode) == 0o700


def test_staged_headers_file_is_content_addressed(tmp_path):
    """Different files never share a staged copy, so concurrent launches with
    different headers do not overwrite each other's."""
    a = _write_headers_file(tmp_path, 'Authorization: Bearer a\n')
    b = tmp_path / 'other'
    b.write_text('Authorization: Bearer b\n', encoding='utf-8')
    staged_a = _staged_path(_agent_with_headers_file(a))
    staged_b = _staged_path(_agent_with_headers_file(str(b)))
    assert staged_a != staged_b
    assert _staged_path(_agent_with_headers_file(a)) == staged_a


@pytest.mark.parametrize('content', ['no-colon-here\n', 'Bad Name: v\n'])
def test_headers_file_malformed(tmp_path, content):
    agent = _agent_with_headers_file(_write_headers_file(tmp_path, content))
    with pytest.raises(exceptions.InvalidSkyPilotConfigError):
        agent.get_credential_file_mounts()


def test_headers_file_missing():
    """Fails before provisioning with a config error, not at setup."""
    agent = _agent_with_headers_file('/nonexistent/otlp_headers')
    with pytest.raises(exceptions.InvalidSkyPilotConfigError,
                       match='/nonexistent/otlp_headers'):
        agent.get_credential_file_mounts()


def test_headers_file_falls_back_to_delivered_copy(tmp_path):
    """A node launching clusters with the server's config (e.g. a jobs
    controller) uses the headers file delivered to it."""
    delivered = _write_headers_file(tmp_path, 'Authorization: Bearer x\n')
    agent = _agent_with_headers_file('/path/only/on/api/server')
    with mock.patch.object(otlp, '_REMOTE_HEADERS_PATH', delivered):
        staged = agent.get_credential_file_mounts()[delivered]
        assert _node_value(staged, 1) == 'Bearer x\n'
        assert _output(agent)['header'] == [
            'Authorization ${SKYPILOT_OTLP_HEADER_0}'
        ]


@pytest.mark.parametrize('store', ['otlp', 'OTLP'])
def test_get_logging_agent_selects_otlp(store):

    def fake_get_nested(keys, default=None):
        if keys == ('logs', 'store'):
            return store
        if keys == ('logs', 'otlp'):
            return {'endpoint': 'http://collector:4318'}
        return default

    with mock.patch('sky.skypilot_config.get_nested',
                    side_effect=fake_get_nested):
        assert isinstance(logs.get_logging_agent(), OtlpLoggingAgent)


def test_config_schema():
    schema = schemas.get_config_schema()
    valid = {
        'logs': {
            'store': 'otlp',
            'otlp': {
                'endpoint': 'https://collector:4318',
                'protocol': 'grpc',
                'headers': {
                    'X-A': 'b'
                },
                'headers_file': '~/otlp_headers',
                'compression': 'gzip',
                'tls': {
                    'insecure_skip_verify': True
                },
                'resource_attributes': {
                    'env': 'prod'
                },
            },
        }
    }
    common_utils.validate_schema(valid, schema, 'Invalid config: ')
    for bad in [{
            'store': 'otlp'
    }, {
            'store': 'OTLP'
    }, {
            'store': 'otlp',
            'otlp': {}
    }, {
            'store': 'otlp',
            'otlp': {
                'endpoint': 'http://c',
                'unknown': 1
            }
    }]:
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            common_utils.validate_schema({'logs': bad}, schema,
                                         'Invalid config: ')
    # Other stores do not need a block of their own.
    common_utils.validate_schema({'logs': {
        'store': 'gcp'
    }}, schema, 'Invalid config: ')
