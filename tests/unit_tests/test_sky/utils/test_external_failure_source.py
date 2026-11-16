"""Unit tests for the ExternalFailureSource plugin extension point."""
import pytest

from sky.utils.plugin_extensions import ExternalFailureSource

_FAILURE = {
    'cluster_hash': 'hash-1',
    'failure_mode': 'NODE_HARDWARE_FAILURE',
    'failure_reason': 'Hardware error on node node-1',
    'cleared_at': None,
}


@pytest.fixture(autouse=True)
def _restore_registration(monkeypatch):
    """Keep each test's registration from leaking into other tests."""
    # pylint: disable=protected-access
    monkeypatch.setattr(ExternalFailureSource, '_get_func',
                        ExternalFailureSource._get_func)
    monkeypatch.setattr(ExternalFailureSource, '_clear_func',
                        ExternalFailureSource._clear_func)
    monkeypatch.setattr(ExternalFailureSource, '_diagnose_job_failure_func',
                        ExternalFailureSource._diagnose_job_failure_func)


def _get(cluster_hash=None, cluster_name=None):
    del cluster_hash, cluster_name
    return []


def _clear(cluster_hash=None, cluster_name=None):
    del cluster_hash, cluster_name
    return []


def test_diagnose_job_failure_without_source():
    """No registered source reports no infrastructure failure."""
    ExternalFailureSource._get_func = None  # pylint: disable=protected-access
    ExternalFailureSource._clear_func = None  # pylint: disable=protected-access
    # pylint: disable=protected-access
    ExternalFailureSource._diagnose_job_failure_func = None

    assert ExternalFailureSource.diagnose_job_failure('my-cluster') == []


def test_diagnose_job_failure_is_optional():
    """A source registered without the check keeps working as before."""
    ExternalFailureSource.register(get_failures=_get, clear_failures=_clear)

    assert ExternalFailureSource.is_registered()
    assert ExternalFailureSource.diagnose_job_failure('my-cluster') == []


def test_diagnose_job_failure_returns_source_records():
    calls = []

    def _diagnose(cluster_name):
        calls.append(cluster_name)
        return [_FAILURE]

    ExternalFailureSource.register(get_failures=_get,
                                   clear_failures=_clear,
                                   diagnose_job_failure=_diagnose)

    assert ExternalFailureSource.diagnose_job_failure('my-cluster') == [
        _FAILURE
    ]
    assert calls == ['my-cluster']


def test_diagnose_job_failure_swallows_source_errors():
    """A failing check must not look like an infrastructure failure."""

    def _diagnose(cluster_name):
        raise RuntimeError(f'cannot reach the source for {cluster_name}')

    ExternalFailureSource.register(get_failures=_get,
                                   clear_failures=_clear,
                                   diagnose_job_failure=_diagnose)

    assert ExternalFailureSource.diagnose_job_failure('my-cluster') == []


def test_reregistering_without_check_removes_it():
    ExternalFailureSource.register(get_failures=_get,
                                   clear_failures=_clear,
                                   diagnose_job_failure=lambda _: [_FAILURE])
    ExternalFailureSource.register(get_failures=_get, clear_failures=_clear)

    assert ExternalFailureSource.diagnose_job_failure('my-cluster') == []
