"""Unit tests for the Scarf analytics ping in sky.usage.usage_lib."""
# pylint: disable=redefined-outer-name,unused-argument,protected-access
from typing import List

import pytest

from sky.skylet import constants as skylet_constants
from sky.usage import usage_lib


@pytest.fixture
def scarf_env(monkeypatch):
    """Gives each test a clean per-context and per-process Scarf state."""
    usage_lib._messages_var.set(None)
    monkeypatch.setattr(usage_lib, '_scarf_pinged_entrypoints', set())
    for env_var in ('SKYPILOT_DISABLE_USAGE_COLLECTION', 'DO_NOT_TRACK',
                    'SCARF_NO_ANALYTICS',
                    skylet_constants.ENV_VAR_IS_SKYPILOT_SERVER):
        monkeypatch.delenv(env_var, raising=False)
    yield monkeypatch
    usage_lib._messages_var.set(None)


@pytest.fixture
def sent_pings(monkeypatch) -> List[dict]:
    """Captures the params of every ping instead of hitting the network."""
    sent: List[dict] = []
    monkeypatch.setattr(usage_lib, '_send_scarf_ping', sent.append)
    return sent


def _ping(command: str = 'sky.client.sdk.launch', internal: bool = False):
    thread = usage_lib._maybe_start_scarf_ping(command, internal)
    if thread is not None:
        thread.join(timeout=5)
    return thread


def test_ping_sent_with_command_and_version(scarf_env, sent_pings):
    assert _ping() is not None
    assert len(sent_pings) == 1
    assert sent_pings[0]['command'] == 'sky.client.sdk.launch'
    assert 'version' in sent_pings[0]


def test_ping_deduplicated_per_process(scarf_env, sent_pings):
    _ping('sky.client.sdk.status')
    _ping('sky.client.sdk.status')
    _ping('sky.client.sdk.launch')
    assert [p['command'] for p in sent_pings
           ] == ['sky.client.sdk.status', 'sky.client.sdk.launch']


@pytest.mark.parametrize('value', ['1', 'true', 'True'])
def test_disable_usage_collection_blocks_ping(scarf_env, sent_pings, value):
    # SKYPILOT_DISABLE_USAGE_COLLECTION accepts 'true'/'1'
    # (case-insensitive), matching env_options.Options.DISABLE_LOGGING.
    scarf_env.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', value)
    assert _ping() is None
    assert not sent_pings


@pytest.mark.parametrize('env_var', ['DO_NOT_TRACK', 'SCARF_NO_ANALYTICS'])
def test_do_not_track_blocks_ping(scarf_env, sent_pings, env_var):
    scarf_env.setenv(env_var, '1')
    assert _ping() is None
    assert not sent_pings


@pytest.mark.parametrize('env_var', ['DO_NOT_TRACK', 'SCARF_NO_ANALYTICS'])
def test_do_not_track_zero_allows_ping(scarf_env, sent_pings, env_var):
    scarf_env.setenv(env_var, '0')
    assert _ping() is not None
    assert len(sent_pings) == 1


def test_no_ping_on_api_server(scarf_env, sent_pings):
    scarf_env.setenv(skylet_constants.ENV_VAR_IS_SKYPILOT_SERVER, 'true')
    assert _ping() is None
    assert not sent_pings


def test_no_ping_for_internal_operations(scarf_env, sent_pings):
    assert _ping(internal=True) is None
    assert not sent_pings


def test_no_ping_when_operation_skipped(scarf_env, sent_pings):
    # E.g. sdk.launch(dryrun=True).
    usage_lib.skip_scarf_ping_for_current_operation()
    assert _ping() is None
    assert not sent_pings


def test_skip_flag_not_reported_to_loki(scarf_env):
    usage_lib.skip_scarf_ping_for_current_operation()
    properties = usage_lib.messages.usage.get_properties()
    assert not any('scarf' in key for key in properties)


def test_entrypoint_pings_at_outermost_exit(scarf_env, sent_pings):
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint
    def my_command():
        return 42

    assert my_command() == 42
    assert len(sent_pings) == 1
    assert sent_pings[0]['command'].endswith('my_command')


def test_entrypoint_pings_only_outermost(scarf_env, sent_pings):
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint
    def inner():
        pass

    @usage_lib.entrypoint
    def outer():
        inner()

    outer()
    assert len(sent_pings) == 1
    assert sent_pings[0]['command'].endswith('outer')


def test_entrypoint_skip_set_inside_body_is_honored(scarf_env, sent_pings):
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint
    def dryrun_command():
        usage_lib.skip_scarf_ping_for_current_operation()

    dryrun_command()
    assert not sent_pings


def test_internal_set_before_entrypoint_suppresses_ping(
        scarf_env, sent_pings):
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint
    def controller_launch():
        pass

    # Controllers call set_internal() before invoking the client SDK.
    usage_lib.messages.usage.set_internal()
    controller_launch()
    assert not sent_pings


def test_internal_set_during_body_does_not_suppress_ping(
        scarf_env, sent_pings):
    # E.g. `sky status` marks its implicit jobs queue sub-query internal;
    # the user-initiated status command should still be reported.
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint
    def status_command():
        usage_lib.messages.usage.set_internal()

    status_command()
    assert len(sent_pings) == 1
    assert sent_pings[0]['command'].endswith('status_command')


def test_fallback_entrypoint_does_not_ping(scarf_env, sent_pings):
    scarf_env.setattr(usage_lib, '_send_to_loki', lambda *args: None)

    @usage_lib.entrypoint('sky.cli', fallback=True)
    def cli_wrapper():
        pass

    cli_wrapper()
    assert not sent_pings
