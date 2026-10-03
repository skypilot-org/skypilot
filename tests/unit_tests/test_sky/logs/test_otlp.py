"""Unit tests for sky.logs.otlp module."""

import os
import subprocess
from unittest import mock

import jsonschema
import pytest

from sky import exceptions
from sky import logs
from sky.logs import otlp
from sky.logs.otlp import _AWK_HEADER_VALUE
from sky.logs.otlp import _parse_headers_file
from sky.logs.otlp import _REMOTE_HEADERS_PATH
from sky.logs.otlp import OtlpLoggingAgent
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas

_CLUSTER_NAME = resources_utils.ClusterName('test-cluster',
                                            'test-cluster-unique-id')


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


def test_protocol_compression_and_tls_verify():
    cfg = _output(
        OtlpLoggingAgent({
            'endpoint': 'https://collector:4317',
            'protocol': 'GRPC',
            'compression': 'gzip',
            'tls_verify': False,
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


def test_headers_file_values_never_leave_the_file(tmp_path):
    path = _write_headers_file(
        tmp_path, '# comment\n\nAuthorization: Bearer s3cret\n'
        'X-Api-Key:k3y\n')
    agent = OtlpLoggingAgent({
        'endpoint': 'http://collector:4318',
        'headers_file': path,
    })
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
    assert agent.get_credential_file_mounts() == {
        _REMOTE_HEADERS_PATH: os.path.realpath(path)
    }


def test_headers_file_awk_extracts_values(tmp_path):
    """The shell extraction on the node matches the server-side parsing."""
    content = ('# comment\n\n'
               'Authorization:   Bearer a b  \r\n'
               '  # indented comment\n'
               'X-Api-Key:k3y:with:colons\n')
    path = _write_headers_file(tmp_path, content)
    for n, expected in [(1, 'Bearer a b'), (2, 'k3y:with:colons')]:
        out = subprocess.run(['awk', '-v', f'n={n}', _AWK_HEADER_VALUE, path],
                             check=True,
                             capture_output=True,
                             text=True).stdout
        assert out == expected + '\n'
    assert _parse_headers_file(path) == ['Authorization', 'X-Api-Key']


@pytest.mark.parametrize('content', ['no-colon-here\n', 'Bad Name: v\n'])
def test_headers_file_malformed(tmp_path, content):
    agent = OtlpLoggingAgent({
        'endpoint': 'http://collector:4318',
        'headers_file': _write_headers_file(tmp_path, content),
    })
    with pytest.raises(exceptions.InvalidSkyPilotConfigError):
        _output(agent)


def test_headers_file_falls_back_to_delivered_copy(tmp_path):
    """A node launching clusters with the server's config (e.g. a jobs
    controller) uses the headers file delivered to it."""
    delivered = _write_headers_file(tmp_path, 'Authorization: Bearer x\n')
    agent = OtlpLoggingAgent({
        'endpoint': 'http://collector:4318',
        'headers_file': '/path/only/on/api/server',
    })
    with mock.patch.object(otlp, '_REMOTE_HEADERS_PATH', delivered):
        assert agent.get_credential_file_mounts() == {delivered: delivered}
        assert _output(agent)['header'] == [
            'Authorization ${SKYPILOT_OTLP_HEADER_0}'
        ]


def test_get_logging_agent_selects_otlp():

    def fake_get_nested(keys, default=None):
        if keys == ('logs', 'store'):
            return 'otlp'
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
                'tls_verify': False,
                'resource_attributes': {
                    'env': 'prod'
                },
            },
        }
    }
    common_utils.validate_schema(valid, schema, 'Invalid config: ')
    for bad in [{
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
