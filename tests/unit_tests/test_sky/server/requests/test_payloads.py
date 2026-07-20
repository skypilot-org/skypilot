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


def test_create_debug_dump_body_overall_deadline_threads_through():
    """overall_deadline must survive to_kwargs so the executor forwards it to
    core.create_debug_dump; omitting it stays None (back-compat)."""
    kwargs = payloads.CreateDebugDumpBody(
        managed_job_ids=[37], overall_deadline=1234567890.0).to_kwargs()
    assert kwargs['overall_deadline'] == 1234567890.0
    assert kwargs['managed_job_ids'] == [37]

    # Omitted -> None, i.e. unchanged (no-deadline) behavior.
    default_kwargs = payloads.CreateDebugDumpBody(
        managed_job_ids=[37]).to_kwargs()
    assert default_kwargs['overall_deadline'] is None


def test_create_debug_dump_body_ignores_unknown_field():
    """Version-skew guard: a newer caller (e.g. a plugin built against a newer
    OSS) may pass a field this model doesn't define yet. BasePayload's
    extra='ignore' must drop it silently -- construct + JSON round-trip must
    not raise -- so a new plugin against an old OSS degrades to a no-op rather
    than crashing every dump.
    """
    body = payloads.CreateDebugDumpBody(
        managed_job_ids=[37], some_future_field_old_oss_lacks='ignored')
    assert not hasattr(body, 'some_future_field_old_oss_lacks')
    assert 'some_future_field_old_oss_lacks' not in body.to_kwargs()
    # The path the request actually takes across replicas: serialize -> store
    # -> reload. Must survive without the unknown field tripping validation.
    reloaded = payloads.CreateDebugDumpBody.model_validate_json(
        body.model_dump_json())
    assert reloaded.managed_job_ids == [37]
