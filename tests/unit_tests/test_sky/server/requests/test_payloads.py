"""Unit tests for sky.server.requests.payloads module."""
import os

import pytest

from sky import skypilot_config
from sky.server.requests import payloads
from sky.skylet import constants
from sky.usage import constants as usage_constants
from sky.usage import usage_lib
from sky.utils import context


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


@pytest.mark.asyncio
async def test_request_body_env_vars_uses_contextual_run_id_when_set(
        monkeypatch):
    """In a JobController-style context, the per-context run id wins over
    the process-wide singleton.

    The internal sky.launch invoked by a managed jobs JobController must
    carry the per-job client run id end-to-end so worker-cluster heartbeats
    can be joined back to the launch command. With the controller process
    serving many jobs in consolidation mode, the process singleton would
    otherwise be the same for every internal launch.

    Async because context.initialize constructs a SkyPilotContext, which
    instantiates an asyncio.Event. On Python 3.9 that requires a running
    event loop, so we let pytest-asyncio supply one.
    """
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'singleton-run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: True)

    original_environ = os.environ
    os.environ = context.ContextualEnviron(original_environ)
    try:
        ctx = context.initialize()
        ctx.override_envs({usage_constants.USAGE_RUN_ID_ENV_VAR: 'ctx-run-id'})
        env_vars = payloads.request_body_env_vars()
    finally:
        os.environ = original_environ

    assert env_vars[usage_constants.USAGE_RUN_ID_ENV_VAR] == 'ctx-run-id'


def test_request_body_env_vars_falls_back_to_singleton_run_id(monkeypatch):
    """Without a context override, the process singleton is still used.

    Preserves CLI / direct-SDK behavior where no SkyPilotContext is
    established and the singleton is initialized at module load.
    """
    monkeypatch.setattr(usage_lib.messages.usage, 'run_id', 'singleton-run-id')
    monkeypatch.setattr(payloads.common, 'is_api_server_local', lambda: True)
    monkeypatch.delenv(usage_constants.USAGE_RUN_ID_ENV_VAR, raising=False)

    env_vars = payloads.request_body_env_vars()
    assert env_vars[usage_constants.USAGE_RUN_ID_ENV_VAR] == 'singleton-run-id'
