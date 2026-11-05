"""How setup_logging_on_cluster reports a failed logging agent setup."""
# pylint: disable=protected-access
from unittest import mock

import pytest

from sky.logs import agent as logs_agent
from sky.provision import instance_setup
from sky.utils import resources_utils


def _setup(monkeypatch, returncode, output):
    """Sets up the agent on one node whose command exits `returncode`.

    `output` is returned as stdout, as the runner merges stderr into it.

    Returns how many times the command ran, and the exception raised.
    """
    calls = []

    def _run(*args, **kwargs):
        del args, kwargs
        calls.append(1)
        return returncode, output, ''

    monkeypatch.setattr(instance_setup, '_hint_worker_log_path', mock.Mock())
    monkeypatch.setattr(instance_setup.time, 'sleep', mock.Mock())
    monkeypatch.setattr(
        instance_setup, '_parallel_ssh_with_cache',
        lambda func, *args, **kwargs: func(mock.Mock(run=_run), '/tmp/l.log'))
    agent = mock.Mock(get_setup_command=mock.Mock(return_value='true'))
    with pytest.raises(RuntimeError) as e:
        instance_setup.setup_logging_on_cluster(
            agent, resources_utils.ClusterName('c', 'c'), mock.Mock(), {})
    return len(calls), e.value


def test_unsupported_platform_is_reported_without_retries(monkeypatch):
    runs, error = _setup(
        monkeypatch, logs_agent.UNSUPPORTED_PLATFORM_EXIT_CODE,
        'SkyPilot: cannot install fluent-bit: no fluent-bit packages for '
        '\'fedora\'\ncommand terminated with exit code 87\n')
    assert isinstance(error, instance_setup.LoggingAgentUnsupportedError)
    assert str(error) == ('cannot install fluent-bit: no fluent-bit packages '
                          'for \'fedora\'')
    assert runs == 1


def test_other_failures_are_retried_and_raised(monkeypatch):
    runs, error = _setup(
        monkeypatch, 1,
        'Neither service account key nor metadata server is available.')
    assert not isinstance(error, instance_setup.LoggingAgentUnsupportedError)
    assert 'Failed to setup logging agent' in str(error)
    assert runs == instance_setup._MAX_RETRY
