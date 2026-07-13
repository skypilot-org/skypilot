"""Tests for the plugin-registerable managed-jobs db-write wrapper.

The hook in ``sky.server.plugins`` lets out-of-tree plugins layer
idempotency / retry / swallow semantics around ``set_failed_async`` /
``set_cancelling_async`` / ``set_cancelled_async`` without forking
``sky.jobs.state``.

These tests pin two contracts:

1. With no wrapper registered, the writers call the underlying impl
   exactly once with the original arguments (i.e. the OSS default
   behavior is preserved bit-for-bit).
2. With a wrapper registered, the writers route through the wrapper,
   and the wrapper receives the impl + the original args so it can
   delegate (or short-circuit).
"""
import asyncio
from unittest import mock

import pytest

from sky.jobs import state as managed_job_state
from sky.server import plugins as server_plugins


@pytest.fixture(autouse=True)
def _reset_wrapper():
    """Ensure each test starts with no wrapper registered."""
    server_plugins.register_managed_jobs_db_write_wrapper(None)
    yield
    server_plugins.register_managed_jobs_db_write_wrapper(None)


def test_get_wrapper_returns_none_by_default():
    assert server_plugins.get_managed_jobs_db_write_wrapper() is None


def test_register_and_get_round_trip():
    sentinel = object()
    server_plugins.register_managed_jobs_db_write_wrapper(sentinel)
    assert server_plugins.get_managed_jobs_db_write_wrapper() is sentinel


@pytest.mark.asyncio
async def test_set_failed_async_no_wrapper_calls_impl_directly():
    """With no wrapper, set_failed_async hits the impl with original args."""
    with mock.patch.object(managed_job_state,
                           '_set_failed_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        await managed_job_state.set_failed_async(
            job_id=42,
            task_id=0,
            failure_type=managed_job_state.ManagedJobStatus.FAILED,
            failure_reason='unit test')
    mock_impl.assert_called_once()
    # The wrapper-less path should pass args+kwargs through unchanged.
    args, kwargs = mock_impl.call_args
    assert args == (42,)
    assert kwargs['task_id'] == 0
    assert kwargs['failure_type'] == managed_job_state.ManagedJobStatus.FAILED
    assert kwargs['failure_reason'] == 'unit test'


@pytest.mark.asyncio
async def test_set_cancelling_async_no_wrapper_calls_impl_directly():
    async def _cb(_status):  # pragma: no cover - never called
        return None

    with mock.patch.object(managed_job_state,
                           '_set_cancelling_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        await managed_job_state.set_cancelling_async(7, _cb)
    mock_impl.assert_called_once_with(7, _cb)


@pytest.mark.asyncio
async def test_set_cancelled_async_no_wrapper_calls_impl_directly():
    async def _cb(_status):  # pragma: no cover - never called
        return None

    with mock.patch.object(managed_job_state,
                           '_set_cancelled_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        await managed_job_state.set_cancelled_async(7, _cb)
    mock_impl.assert_called_once_with(7, _cb)


@pytest.mark.asyncio
async def test_set_failed_async_routes_through_wrapper_when_registered():
    """When a wrapper is registered, set_failed_async delegates to it."""
    calls = []

    async def wrapper(impl, *args, **kwargs):
        calls.append((impl, args, kwargs))
        # Sentinel: don't actually call the impl in the test. A real
        # wrapper would (and should) call ``await impl(*args, **kwargs)``.
        return 'wrapped-sentinel'

    server_plugins.register_managed_jobs_db_write_wrapper(wrapper)

    with mock.patch.object(managed_job_state,
                           '_set_failed_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        result = await managed_job_state.set_failed_async(
            job_id=99,
            task_id=None,
            failure_type=managed_job_state.ManagedJobStatus.FAILED_CONTROLLER,
            failure_reason='wrapper test')

    assert result == 'wrapped-sentinel'
    # Wrapper called; impl NOT directly called by set_failed_async (the
    # wrapper itself is responsible for calling it — and in this test it
    # deliberately doesn't, to confirm the routing).
    assert len(calls) == 1
    delegated_impl, args, kwargs = calls[0]
    # The public ``set_failed_async`` passes the module-level
    # ``_set_failed_async_impl`` attribute to the wrapper; we don't pin the
    # identity here (mock.patch.object replaces the attribute), only that
    # the args were forwarded correctly.
    assert delegated_impl is mock_impl
    # ``job_id`` is passed positionally to the wrapper; the rest are kwargs.
    assert args == (99,)
    assert kwargs['task_id'] is None
    assert (kwargs['failure_type']
            == managed_job_state.ManagedJobStatus.FAILED_CONTROLLER)
    assert kwargs['failure_reason'] == 'wrapper test'
    mock_impl.assert_not_called()


@pytest.mark.asyncio
async def test_set_cancelling_async_routes_through_wrapper_when_registered():
    calls = []

    async def wrapper(impl, *args, **kwargs):
        calls.append((impl, args, kwargs))
        return None

    server_plugins.register_managed_jobs_db_write_wrapper(wrapper)

    async def _cb(_status):  # pragma: no cover - never called
        return None

    with mock.patch.object(managed_job_state,
                           '_set_cancelling_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        await managed_job_state.set_cancelling_async(11, _cb)

    assert len(calls) == 1
    delegated_impl, args, _kwargs = calls[0]
    assert delegated_impl is mock_impl
    assert args == (11, _cb)
    mock_impl.assert_not_called()


@pytest.mark.asyncio
async def test_set_cancelled_async_routes_through_wrapper_when_registered():
    calls = []

    async def wrapper(impl, *args, **kwargs):
        calls.append((impl, args, kwargs))
        return None

    server_plugins.register_managed_jobs_db_write_wrapper(wrapper)

    async def _cb(_status):  # pragma: no cover - never called
        return None

    with mock.patch.object(managed_job_state,
                           '_set_cancelled_async_impl',
                           new=mock.AsyncMock(
                               return_value=None)) as mock_impl:
        await managed_job_state.set_cancelled_async(11, _cb)

    assert len(calls) == 1
    delegated_impl, args, _kwargs = calls[0]
    assert delegated_impl is mock_impl
    assert args == (11, _cb)
    mock_impl.assert_not_called()


@pytest.mark.asyncio
async def test_wrapper_can_delegate_to_impl():
    """Smoke-test that a wrapper which calls impl(*args, **kwargs) works."""

    async def wrapper(impl, *args, **kwargs):
        return await impl(*args, **kwargs)

    server_plugins.register_managed_jobs_db_write_wrapper(wrapper)

    with mock.patch.object(managed_job_state,
                           '_set_failed_async_impl',
                           new=mock.AsyncMock(
                               return_value='delegated')) as mock_impl:
        result = await managed_job_state.set_failed_async(
            job_id=1,
            task_id=None,
            failure_type=managed_job_state.ManagedJobStatus.FAILED,
            failure_reason='delegate')

    assert result == 'delegated'
    mock_impl.assert_called_once()
