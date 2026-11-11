"""Tests for the opt-in switch of the deprecated managed-job event_callback."""
import asyncio
from unittest import mock

import pytest

from sky import dag as dag_lib
from sky import skypilot_config
from sky import task as task_lib
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants


@pytest.fixture
def no_config(monkeypatch):
    """Start every test with no `jobs.allow_event_callback` set."""
    monkeypatch.setattr(skypilot_config,
                        'get_nested',
                        lambda keys, default_value=None, **_: default_value)


def _set_config(monkeypatch, value):
    values = {('jobs', 'allow_event_callback'): value}
    monkeypatch.setattr(skypilot_config,
                        'get_nested',
                        lambda keys, default_value=None, **_: values.get(
                            tuple(keys), default_value))


def _in_api_server(monkeypatch, in_server: bool):
    if in_server:
        monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, 'true')
    else:
        monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)


@pytest.mark.usefixtures('no_config')
@pytest.mark.parametrize('in_server,expected', [
    (True, False),
    (False, True),
])
def test_default(monkeypatch, in_server, expected):
    """Unset config: off in the API server (and its consolidation-mode
    controller); allowed in a controller on its own cluster, whose jobs the
    API server already checked at submission."""
    _in_api_server(monkeypatch, in_server)
    assert managed_job_utils.event_callbacks_allowed() is expected


@pytest.mark.parametrize('in_server', [True, False])
@pytest.mark.parametrize('config_value', [True, False])
def test_explicit_config_wins(monkeypatch, in_server, config_value):
    _in_api_server(monkeypatch, in_server)
    _set_config(monkeypatch, config_value)
    assert managed_job_utils.event_callbacks_allowed() is config_value


def _dag_with_callback(callback):
    dag = dag_lib.Dag()
    task = task_lib.Task(run='echo hi', event_callback=callback)
    dag.add(task)
    return dag


def test_submission_rejected_by_default(monkeypatch):
    _in_api_server(monkeypatch, True)
    _set_config(monkeypatch, None)
    with pytest.raises(ValueError, match='allow_event_callback'):
        managed_job_utils.check_event_callbacks_allowed(
            _dag_with_callback('echo $JOB_STATUS'))


def test_submission_without_callback_is_fine_by_default(monkeypatch):
    _in_api_server(monkeypatch, True)
    _set_config(monkeypatch, None)
    managed_job_utils.check_event_callbacks_allowed(_dag_with_callback(None))


def test_submission_allowed_when_enabled_warns_deprecated(monkeypatch):
    _in_api_server(monkeypatch, True)
    _set_config(monkeypatch, True)
    with mock.patch.object(managed_job_utils.logger, 'warning') as warning:
        managed_job_utils.check_event_callbacks_allowed(
            _dag_with_callback('echo $JOB_STATUS'))
    warning.assert_called_once()
    assert 'deprecated' in warning.call_args[0][0]


@pytest.mark.parametrize('allowed,runs', [(False, False), (True, True)])
def test_queued_job_callback_skipped_when_disabled(monkeypatch, allowed, runs):
    """Jobs submitted before callbacks were disabled must not run them."""
    monkeypatch.setattr(managed_job_utils,
                        '_controller_event_callbacks_allowed', None)
    monkeypatch.setattr(managed_job_utils, 'event_callbacks_allowed',
                        lambda: allowed)
    monkeypatch.setattr(managed_job_utils.managed_job_state,
                        'get_pool_from_job_id', lambda job_id: None)
    task = task_lib.Task(name='t',
                         run='echo hi',
                         event_callback='echo $JOB_STATUS')
    with mock.patch.object(managed_job_utils.log_lib,
                           'run_bash_command_with_log',
                           return_value=0) as run_bash:
        callback = managed_job_utils.event_callback_func(1, 0, task)
        asyncio.run(callback('RUNNING'))
    assert run_bash.called is runs


def test_controller_snapshot_wins_over_job_config(monkeypatch):
    """The controller's start-time decision beats a job's stale snapshot."""
    monkeypatch.setattr(managed_job_utils, 'event_callbacks_allowed',
                        lambda: True)  # what a stale job config would say
    monkeypatch.setattr(managed_job_utils,
                        '_controller_event_callbacks_allowed', False)
    monkeypatch.setattr(managed_job_utils.managed_job_state,
                        'get_pool_from_job_id', lambda job_id: None)
    task = task_lib.Task(name='t',
                         run='echo hi',
                         event_callback='echo $JOB_STATUS')
    with mock.patch.object(managed_job_utils.log_lib,
                           'run_bash_command_with_log',
                           return_value=0) as run_bash:
        asyncio.run(
            managed_job_utils.event_callback_func(1, 0, task)('RUNNING'))
    run_bash.assert_not_called()
