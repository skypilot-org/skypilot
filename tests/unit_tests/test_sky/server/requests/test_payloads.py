"""Unit tests for sky.server.requests.payloads module."""
from sky import skypilot_config
from sky.server.requests import payloads
from sky.skylet import constants
from sky.usage import usage_lib


def test_request_body_env_vars_includes_expected_keys(monkeypatch):
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')

    server_env = f'{constants.SKYPILOT_SERVER_ENV_VAR_PREFIX}BAR'
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       '/tmp/config.yaml')
    monkeypatch.setenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG,
                       '/tmp/global.yaml')
    monkeypatch.setenv(skypilot_config.ENV_VAR_PROJECT_CONFIG,
                       '/tmp/project.yaml')
    monkeypatch.setenv(constants.ENV_VAR_DB_CONNECTION_URI, 'db-uri')

    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: True)
    local_env = payloads.request_body_env_vars()
    assert server_env not in local_env
    assert local_env[
        skypilot_config.ENV_VAR_SKYPILOT_CONFIG] == '/tmp/config.yaml'
    assert constants.ENV_VAR_DB_CONNECTION_URI not in local_env
    assert skypilot_config.ENV_VAR_GLOBAL_CONFIG not in local_env
    assert skypilot_config.ENV_VAR_PROJECT_CONFIG not in local_env

    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: False)
    remote_env = payloads.request_body_env_vars()
    assert 'AWS_PROFILE' not in remote_env
    assert skypilot_config.ENV_VAR_SKYPILOT_CONFIG not in remote_env
    assert skypilot_config.ENV_VAR_GLOBAL_CONFIG not in remote_env
    assert skypilot_config.ENV_VAR_PROJECT_CONFIG not in remote_env
    assert constants.CLIENT_USER_HASH_ENV_VAR not in remote_env


def test_request_body_env_vars_client_user_hash_with_basic_auth(monkeypatch):
    """client user hash env var is included when basic auth is enabled."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: True)
    monkeypatch.setattr(payloads.common, 'basic_auth_enabled', True)
    monkeypatch.setattr(payloads.common, 'client_user_hash', 'abcd1234')

    env_vars = payloads.request_body_env_vars()
    assert env_vars[constants.CLIENT_USER_HASH_ENV_VAR] == 'abcd1234'


def test_request_body_env_vars_client_user_hash_none_with_basic_auth(
        monkeypatch):
    """client user hash env var is skipped when basic auth is enabled but hash is None."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: True)
    monkeypatch.setattr(payloads.common, 'basic_auth_enabled', True)
    monkeypatch.setattr(payloads.common, 'client_user_hash', None)

    env_vars = payloads.request_body_env_vars()
    assert constants.CLIENT_USER_HASH_ENV_VAR not in env_vars


def test_request_body_env_vars_excludes_server_prefix(monkeypatch):
    """Vars with SKYPILOT_SERVER_ prefix are excluded from forwarding."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: False)
    monkeypatch.setenv(constants.SKYPILOT_SERVER_APISERVER_UUID_ENV_VAR,
                       'pod-uid-xyz')
    monkeypatch.setenv(constants.SKYPILOT_SERVER_POD_MEMORY_BYTES_LIMIT_ENV_VAR,
                       '104857600')
    monkeypatch.setenv(
        constants.SKYPILOT_SERVER_AUTH_OAUTH2_PROXY_ENABLED_ENV_VAR, 'true')

    env_vars = payloads.request_body_env_vars()
    assert constants.SKYPILOT_SERVER_APISERVER_UUID_ENV_VAR not in env_vars
    assert (constants.SKYPILOT_SERVER_POD_MEMORY_BYTES_LIMIT_ENV_VAR
            not in env_vars)
    assert (constants.SKYPILOT_SERVER_AUTH_OAUTH2_PROXY_ENABLED_ENV_VAR
            not in env_vars)


def test_request_body_env_vars_excludes_k8s_service_link_envs(monkeypatch):
    """K8s service-link injected envs under SKYPILOT_AGENT_ are denied."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: False)
    # Service-link env names K8s injects for a SkyPilot agent Service named
    # `skypilot-agent-abcd1234-head-ssh`.
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_SERVICE_HOST',
                       '10.0.0.1')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_SERVICE_PORT', '22')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_PORT',
                       'tcp://10.0.0.1:22')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_PORT_22_TCP_PROTO',
                       'tcp')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_PORT_22_TCP_PORT',
                       '22')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_PORT_22_TCP_ADDR',
                       '10.0.0.1')
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_PORT_22_TCP',
                       'tcp://10.0.0.1:22')
    # Named-port variant: SERVICE_PORT_<PORTNAME>=<port>.
    monkeypatch.setenv('SKYPILOT_AGENT_ABCD1234_HEAD_SSH_SERVICE_PORT_SSH',
                       '22')

    env_vars = payloads.request_body_env_vars()
    for k in list(env_vars):
        assert not k.startswith('SKYPILOT_AGENT_'), (
            f'Service-link env {k} should not have been forwarded')


def test_request_body_env_vars_keeps_legitimate_agent_envs(monkeypatch):
    """Plugin-defined SKYPILOT_AGENT_* env vars without K8s service-link
    suffix are NOT filtered out by the deny-list."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: False)
    monkeypatch.setenv('SKYPILOT_AGENT_ID', 'some-agent-id')
    monkeypatch.setenv('SKYPILOT_AGENT_JWT_SECRET', 'shh')
    monkeypatch.setenv('SKYPILOT_AGENT_API_SERVER_URL',
                       'http://api.example.com')

    env_vars = payloads.request_body_env_vars()
    assert env_vars.get('SKYPILOT_AGENT_ID') == 'some-agent-id'
    assert env_vars.get('SKYPILOT_AGENT_JWT_SECRET') == 'shh'
    assert env_vars.get('SKYPILOT_AGENT_API_SERVER_URL') == \
        'http://api.example.com'


def test_request_body_env_vars_legacy_server_only_var_still_leaks(monkeypatch):
    """Sanity: setting a legacy bare-name server var still leaks. This is
    the regression scenario the rename + reader fallback addresses; if it
    silently stops leaking, the prefix filter has subtly changed."""
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: False)
    monkeypatch.setenv(constants.LEGACY_SKYPILOT_APISERVER_UUID_ENV_VAR,
                       'old-pod-uid')

    env_vars = payloads.request_body_env_vars()
    # Legacy bare name still matches SKYPILOT_ prefix without SKYPILOT_SERVER_.
    assert env_vars.get(
        constants.LEGACY_SKYPILOT_APISERVER_UUID_ENV_VAR) == 'old-pod-uid'
