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
