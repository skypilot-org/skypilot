"""Tests for Kubernetes pod health issue detection."""
from typing import Optional
from unittest import mock

import pytest

from sky.provision.kubernetes.instance import _get_pod_health_issues


def _make_condition(type_: str,
                    status: str,
                    reason: str = '',
                    message: str = ''):
    """Create a mock pod condition."""
    cond = mock.MagicMock()
    cond.type = type_
    cond.status = status
    cond.reason = reason
    cond.message = message
    return cond


def _make_container_status(ready: bool,
                           waiting_reason: Optional[str] = None,
                           terminated_exit_code: Optional[int] = None,
                           name: str = 'ray-node'):
    """Create a mock container status."""
    cs = mock.MagicMock()
    cs.name = name
    cs.ready = ready

    if waiting_reason is not None:
        cs.state.waiting.reason = waiting_reason
        cs.state.terminated = None
    elif terminated_exit_code is not None:
        cs.state.terminated.exit_code = terminated_exit_code
        cs.state.terminated.reason = f'exit({terminated_exit_code})'
        cs.state.waiting = None
    else:
        cs.state.waiting = None
        cs.state.terminated = None
    return cs


def _make_pod(conditions, container_statuses=None):
    """Create a mock pod object."""
    pod = mock.MagicMock()
    pod.status.conditions = conditions
    pod.status.container_statuses = container_statuses or []
    return pod


class TestGetPodHealthIssues:

    def test_healthy_pod_returns_none(self):
        pod = _make_pod(
            conditions=[_make_condition('Ready', 'True')],
            container_statuses=[_make_container_status(ready=True)],
        )
        assert _get_pod_health_issues(pod) is None

    def test_ready_false_returns_reason(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[_make_container_status(ready=False)],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'ContainersNotReady' in result

    def test_crashloopbackoff(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='CrashLoopBackOff'),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'CrashLoopBackOff' in result

    def test_image_pull_backoff(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='ImagePullBackOff'),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert 'ImagePullBackOff' in result

    def test_container_terminated_nonzero(self):
        pod = _make_pod(
            conditions=[
                _make_condition('Ready', 'False', reason='ContainersNotReady')
            ],
            container_statuses=[
                _make_container_status(ready=False, terminated_exit_code=137),
            ],
        )
        result = _get_pod_health_issues(pod)
        assert result is not None
        assert '137' in result

    def test_no_conditions_returns_none(self):
        pod = _make_pod(conditions=None)
        assert _get_pod_health_issues(pod) is None

    def test_ready_true_but_container_waiting(self):
        pod = _make_pod(
            conditions=[_make_condition('Ready', 'True')],
            container_statuses=[
                _make_container_status(ready=False,
                                       waiting_reason='ContainerCreating'),
            ],
        )
        assert _get_pod_health_issues(pod) is None
