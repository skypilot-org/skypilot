"""Unit tests for sky.jobs.runtime: chain registry + dispatch.

Covers ``register``, ``replace``, ``_is_v1_candidate``, and the
module-level dispatch's short-circuit on legacy handles.
"""
# pylint: disable=protected-access,missing-class-docstring
import types
from unittest import mock

import pytest

from sky.jobs import runtime as runtime_chain


@pytest.fixture(autouse=True)
def _reset_runtimes(monkeypatch):
    """Isolate ``_runtimes`` per test so registrations don't leak."""
    monkeypatch.setattr(runtime_chain, '_runtimes', [])
    yield


def _make_handle(has_ray=False, metadata_present=True):
    """Build a fake handle with the attributes the chain inspects."""
    handle = types.SimpleNamespace()
    if metadata_present:
        handle.provision_runtime_metadata = types.SimpleNamespace(
            has_ray=has_ray)
    return handle


def _make_runtime(name, owns_return=False, hook_return=None):
    """Build a mock ``ManagedJobRuntime`` with named owns/get_job_status."""
    rt = mock.MagicMock(name=name)
    rt.owns = mock.MagicMock(return_value=owns_return)
    rt.get_job_status = mock.MagicMock(return_value=hook_return)
    rt.get_job_submitted_at = mock.MagicMock(return_value=None)
    rt.get_job_ended_at = mock.MagicMock(return_value=None)
    rt.get_exit_codes = mock.MagicMock(return_value=None)
    rt.download_logs = mock.MagicMock(return_value=None)
    rt.tail_logs = mock.MagicMock(return_value=None)
    rt.job_group_envs = mock.MagicMock(return_value=None)
    rt.k8s_dns_addresses_for_task = mock.MagicMock(return_value=None)
    rt.k8s_dns_addresses_for_handle = mock.MagicMock(return_value=None)
    return rt


class TestRegister:
    """register() appends in registration order."""

    def test_register_empty(self):
        assert not runtime_chain.is_registered()
        rt = _make_runtime('first')
        runtime_chain.register(rt)
        assert runtime_chain.is_registered()
        assert runtime_chain._runtimes == [rt]

    def test_register_preserves_order(self):
        rt_a = _make_runtime('a')
        rt_b = _make_runtime('b')
        rt_c = _make_runtime('c')
        runtime_chain.register(rt_a)
        runtime_chain.register(rt_b)
        runtime_chain.register(rt_c)
        assert runtime_chain._runtimes == [rt_a, rt_b, rt_c]


class _RuntimeA:
    """Concrete class for replace() tests (isinstance check)."""


class _RuntimeB:
    pass


class TestReplace:
    """replace() swaps in place; appends if absent."""

    def test_replace_swaps_in_place(self):
        old = _RuntimeA()
        new = _RuntimeA()
        other = _make_runtime('other')
        runtime_chain.register(old)
        runtime_chain.register(other)

        runtime_chain.replace(_RuntimeA, new)

        assert runtime_chain._runtimes == [new, other]
        # Index 0 must be the *new* instance, not the old.
        assert runtime_chain._runtimes[0] is new
        assert runtime_chain._runtimes[0] is not old

    def test_replace_appends_when_old_class_absent(self):
        runtime_b = _RuntimeB()
        runtime_chain.register(runtime_b)
        replacement = _RuntimeA()

        runtime_chain.replace(_RuntimeA, replacement)

        # _RuntimeA wasn't present, so we appended.
        assert runtime_chain._runtimes == [runtime_b, replacement]

    def test_replace_only_swaps_first_match(self):
        # Two instances of the same class — replace() targets the first.
        first = _RuntimeA()
        second = _RuntimeA()
        runtime_chain.register(first)
        runtime_chain.register(second)
        new = _RuntimeA()

        runtime_chain.replace(_RuntimeA, new)

        assert runtime_chain._runtimes == [new, second]


class TestIsV1Candidate:
    """_is_v1_candidate's default-to-legacy posture."""

    def test_none_handle_is_not_v1(self):
        assert runtime_chain._is_v1_candidate(None) is False

    def test_has_ray_true_is_not_v1(self):
        handle = _make_handle(has_ray=True)
        assert runtime_chain._is_v1_candidate(handle) is False

    def test_has_ray_false_is_v1(self):
        handle = _make_handle(has_ray=False)
        assert runtime_chain._is_v1_candidate(handle) is True

    def test_missing_metadata_is_not_v1(self):
        """Pre-v1 handles (no ``provision_runtime_metadata``) default to
        legacy — ``getattr(..., 'has_ray', True)``."""
        handle = _make_handle(metadata_present=False)
        assert runtime_chain._is_v1_candidate(handle) is False

    def test_metadata_without_has_ray_is_not_v1(self):
        """Even if metadata exists, missing ``has_ray`` attr → legacy."""
        handle = types.SimpleNamespace()
        # ``provision_runtime_metadata`` present but lacks ``has_ray``.
        handle.provision_runtime_metadata = types.SimpleNamespace()
        assert runtime_chain._is_v1_candidate(handle) is False


class TestDispatchShortCircuit:
    """Module-level dispatch must skip ``owns`` for legacy handles."""

    def test_legacy_handle_does_not_call_owns_or_hooks(self):
        rt_a = _make_runtime('a',
                             owns_return=True,
                             hook_return=('would-fire', None))
        rt_b = _make_runtime('b',
                             owns_return=True,
                             hook_return=('would-fire', None))
        runtime_chain.register(rt_a)
        runtime_chain.register(rt_b)

        legacy_handle = _make_handle(has_ray=True)
        result = runtime_chain.get_job_status(legacy_handle, 'cluster')

        assert result is None
        rt_a.owns.assert_not_called()
        rt_b.owns.assert_not_called()
        rt_a.get_job_status.assert_not_called()
        rt_b.get_job_status.assert_not_called()

    def test_none_handle_short_circuits(self):
        rt = _make_runtime('only',
                           owns_return=True,
                           hook_return=('would-fire', None))
        runtime_chain.register(rt)

        assert runtime_chain.get_job_status(None, 'cluster') is None
        rt.owns.assert_not_called()
        rt.get_job_status.assert_not_called()


class TestDispatchClaiming:
    """v1 handle dispatch resolves in registration order; first non-None
    wins; non-claimants' hooks are not invoked."""

    def test_no_claimant_returns_none(self):
        rt = _make_runtime('only', owns_return=False)
        runtime_chain.register(rt)
        handle = _make_handle(has_ray=False)

        assert runtime_chain.get_job_status(handle, 'cluster') is None
        # owns() *was* called (the chain has to ask) but the hook wasn't.
        rt.owns.assert_called_once_with(handle)
        rt.get_job_status.assert_not_called()

    def test_first_claimant_wins(self):
        rt_a = _make_runtime('a',
                             owns_return=True,
                             hook_return=('A-fired', None))
        rt_b = _make_runtime('b',
                             owns_return=True,
                             hook_return=('B-fired', None))
        runtime_chain.register(rt_a)
        runtime_chain.register(rt_b)
        handle = _make_handle(has_ray=False)

        result = runtime_chain.get_job_status(handle, 'cluster')

        assert result == ('A-fired', None)
        # Per docstring, later runtimes' owns *may* be called — but their
        # hooks must NOT be once an earlier runtime has returned non-None.
        rt_a.get_job_status.assert_called_once()
        rt_b.get_job_status.assert_not_called()

    def test_second_claims_when_first_does_not(self):
        rt_a = _make_runtime('a',
                             owns_return=False,
                             hook_return=('A-fired', None))
        rt_b = _make_runtime('b',
                             owns_return=True,
                             hook_return=('B-fired', None))
        runtime_chain.register(rt_a)
        runtime_chain.register(rt_b)
        handle = _make_handle(has_ray=False)

        result = runtime_chain.get_job_status(handle, 'cluster')

        assert result == ('B-fired', None)
        # First runtime did not claim → its hook is not called.
        rt_a.get_job_status.assert_not_called()
        rt_b.get_job_status.assert_called_once()

    def test_claimant_returning_none_falls_through(self):
        """A claimant whose hook returns None lets the next claimant try."""
        rt_a = _make_runtime('a', owns_return=True, hook_return=None)
        rt_b = _make_runtime('b',
                             owns_return=True,
                             hook_return=('B-fired', None))
        runtime_chain.register(rt_a)
        runtime_chain.register(rt_b)
        handle = _make_handle(has_ray=False)

        result = runtime_chain.get_job_status(handle, 'cluster')

        assert result == ('B-fired', None)
        rt_a.get_job_status.assert_called_once()
        rt_b.get_job_status.assert_called_once()


class TestIsRegistered:

    def test_empty(self):
        assert runtime_chain.is_registered() is False

    def test_after_register(self):
        runtime_chain.register(_make_runtime('rt'))
        assert runtime_chain.is_registered() is True
