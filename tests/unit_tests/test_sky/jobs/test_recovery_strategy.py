"""Unit tests for sky.jobs.recovery_strategy helpers."""
import ast
import asyncio
import inspect
import types
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs import recovery_strategy
from sky.jobs import scheduler as scheduler_module


def test_is_oom_failure_detects_oomkilled():
    exc = RuntimeError(
        'Failed to run setup commands on an instance. (exit code 1). '
        'Pod p terminated: OOMKilled (exit code 137).')
    assert recovery_strategy._is_oom_failure(exc) is True


def test_is_oom_failure_detects_out_of_memory_phrase():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('The container ran out of memory.')) is True


def test_is_oom_failure_is_case_insensitive():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('reason: oomkilled')) is True


def test_is_oom_failure_false_for_unrelated():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('/bin/bash: line 1: conda: command not found')) is False


# ---------------------------------------------------------------------------
# Parked launch request handling (yield the launch slot while the underlying
# launch request is WAITING).
# ---------------------------------------------------------------------------


def _make_bare_executor():
    executor = recovery_strategy.StrategyExecutor.__new__(
        recovery_strategy.StrategyExecutor)
    return executor


def _request_payload(status: str, status_msg=None):
    return types.SimpleNamespace(status=status, status_msg=status_msg)


async def _cleanup_task(task):
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # pylint: disable=broad-except
        pass


@pytest.mark.asyncio
async def test_await_launch_request_returns_on_stream_completion(monkeypatch):
    executor = _make_bare_executor()
    stream_task = asyncio.get_running_loop().create_future()
    stream_task.set_result('result')

    api_status = mock.MagicMock()
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)

    assert await executor._await_launch_request('req-1', stream_task) is None
    api_status.assert_not_called()


@pytest.mark.asyncio
async def test_await_launch_request_propagates_stream_exception(monkeypatch):
    executor = _make_bare_executor()
    stream_task = asyncio.get_running_loop().create_future()
    stream_task.set_exception(ValueError('launch failed'))

    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', mock.MagicMock())

    with pytest.raises(ValueError, match='launch failed'):
        await executor._await_launch_request('req-1', stream_task)


@pytest.mark.asyncio
async def test_await_launch_request_parks_on_waiting_status(monkeypatch):
    executor = _make_bare_executor()
    # A stream that never completes on its own (the launch request is parked
    # server-side; the server keeps the stream open with heartbeats).
    stream_task = asyncio.create_task(asyncio.Event().wait())

    api_status = mock.MagicMock(return_value=[
        _request_payload('WAITING', 'Workload is pending on queue foo.')
    ])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_LAUNCH_REQUEST_STATUS_POLL_SECONDS', 0.01)

    with pytest.raises(recovery_strategy._LaunchRequestParked) as exc_info:
        await executor._await_launch_request('req-1', stream_task)
    assert exc_info.value.request_id == 'req-1'
    assert exc_info.value.status_msg == 'Workload is pending on queue foo.'
    # The stream must be left RUNNING across the park: it cannot be
    # interrupted, and the caller carries it to re-await on resume.
    assert not stream_task.done()
    assert not stream_task.cancelled()
    await _cleanup_task(stream_task)


@pytest.mark.asyncio
async def test_await_launch_request_tolerates_poll_failures(monkeypatch):
    executor = _make_bare_executor()

    async def complete_soon():
        await asyncio.sleep(0.05)
        return 'result'

    stream_task = asyncio.create_task(complete_soon())
    api_status = mock.MagicMock(side_effect=RuntimeError('transient'))
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_LAUNCH_REQUEST_STATUS_POLL_SECONDS', 0.01)

    # Should not raise despite the status poll failing.
    assert await executor._await_launch_request('req-1', stream_task) is None
    assert api_status.call_count >= 1


@pytest.mark.asyncio
async def test_await_launch_request_tolerates_unknown_request(monkeypatch):
    executor = _make_bare_executor()

    async def complete_soon():
        await asyncio.sleep(0.05)
        return 'result'

    stream_task = asyncio.create_task(complete_soon())
    api_status = mock.MagicMock(return_value=[])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_LAUNCH_REQUEST_STATUS_POLL_SECONDS', 0.01)

    assert await executor._await_launch_request('req-1', stream_task) is None


@pytest.mark.asyncio
async def test_wait_for_parked_request_returns_on_resume(monkeypatch):
    executor = _make_bare_executor()
    api_status = mock.MagicMock(side_effect=[
        [_request_payload('WAITING')],
        [_request_payload('RUNNING')],
    ])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_PARKED_POLL_INITIAL_BACKOFF_SECONDS', 0.01)

    assert await executor._wait_for_parked_request('req-1') == 'req-1'
    assert api_status.call_count == 2


@pytest.mark.asyncio
async def test_wait_for_parked_request_relaunches_when_request_gone(
        monkeypatch):
    executor = _make_bare_executor()
    executor._cancel_launch_request = mock.AsyncMock()
    api_status = mock.MagicMock(return_value=[])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_PARKED_POLL_INITIAL_BACKOFF_SECONDS', 0.01)

    assert await executor._wait_for_parked_request('req-1') is None
    # Multiple consecutive misses are required before concluding the request
    # is gone (a single miss can be a transient server hiccup), and the old
    # request is best-effort cancelled before falling back to a fresh launch.
    assert (api_status.call_count ==
            recovery_strategy._PARKED_POLL_MAX_CONSECUTIVE_MISSING)
    executor._cancel_launch_request.assert_awaited_once_with('req-1')


@pytest.mark.asyncio
async def test_wait_for_parked_request_tolerates_transient_missing(monkeypatch):
    executor = _make_bare_executor()
    api_status = mock.MagicMock(side_effect=[
        [],
        [_request_payload('WAITING')],
        [_request_payload('RUNNING')],
    ])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_PARKED_POLL_INITIAL_BACKOFF_SECONDS', 0.01)

    assert await executor._wait_for_parked_request('req-1') == 'req-1'


@pytest.mark.asyncio
async def test_wait_for_parked_request_raises_on_persistent_poll_errors(
        monkeypatch):
    """Persistent poll failures must propagate, not fall back to a fresh
    launch: the old (still parked) request may resume once the server
    becomes reachable again, so treating "unreachable" the same as
    "vanished" could double-launch on the same cluster (fix 5)."""
    executor = _make_bare_executor()
    executor._cancel_launch_request = mock.AsyncMock()
    api_status = mock.MagicMock(side_effect=RuntimeError('server down'))
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    monkeypatch.setattr(recovery_strategy,
                        '_PARKED_POLL_INITIAL_BACKOFF_SECONDS', 0.01)

    with pytest.raises(RuntimeError, match='server down'):
        await executor._wait_for_parked_request('req-1')
    assert (api_status.call_count ==
            recovery_strategy._PARKED_POLL_MAX_CONSECUTIVE_ERRORS)
    # Cancelling is now the caller's responsibility (the generic exception
    # handler in _launch, see fix 4), not _wait_for_parked_request's - it no
    # longer reaches its own best-effort cancel/return-None tail.
    executor._cancel_launch_request.assert_not_awaited()


def _make_launch_executor():
    """Build a minimally-initialized StrategyExecutor for _launch tests."""
    executor = _make_bare_executor()
    executor.job_id = 1
    executor.task_id = 0
    executor.pool = None
    executor.cluster_name = 'test-cluster'
    executor.dag = mock.MagicMock()
    executor.file_mounts_blob_id = None
    executor.starting = set()
    lock = asyncio.Lock()
    executor.starting_lock = lock
    executor.starting_signal = asyncio.Condition(lock)
    executor.RETRY_INIT_GAP_SECONDS = 0.01
    executor._cleanup_cluster = mock.MagicMock()
    executor._wait_until_job_starts_on_cluster = mock.AsyncMock(
        return_value=(123.45, None))
    # Each (fresh) launch attempt gets a stream future; capture them so tests
    # can assert the same future is carried across a park.
    executor._stream_futures = []

    def fake_start_stream_task(request_id):  # pylint: disable=unused-argument
        fut = asyncio.get_running_loop().create_future()
        executor._stream_futures.append(fut)
        return fut

    executor._start_stream_task = mock.MagicMock(
        side_effect=fake_start_stream_task)
    return executor


def _patch_launch_environment(monkeypatch):
    """Patch the scheduler/state/sdk plumbing used by _launch."""
    monkeypatch.setattr(scheduler_module.state, 'get_pool_from_job_id',
                        lambda job_id: None)
    monkeypatch.setattr(scheduler_module.file_content_utils,
                        'get_job_dag_content', lambda job_id: None)
    monkeypatch.setattr(scheduler_module.state, 'scheduler_set_launching_async',
                        mock.AsyncMock())
    monkeypatch.setattr(scheduler_module.state, 'scheduler_set_alive_async',
                        mock.AsyncMock())
    set_restarting = mock.AsyncMock()
    set_backoff_pending = mock.AsyncMock()
    monkeypatch.setattr(recovery_strategy.state, 'set_restarting_async',
                        set_restarting)
    monkeypatch.setattr(recovery_strategy.state, 'set_backoff_pending_async',
                        set_backoff_pending)
    monkeypatch.setattr(recovery_strategy.sdk, 'api_start', mock.MagicMock())
    sdk_launch = mock.MagicMock(return_value='req-123')
    monkeypatch.setattr(recovery_strategy.sdk, 'launch', sdk_launch)
    monkeypatch.setattr(recovery_strategy.global_user_state,
                        'get_handle_from_cluster_name', lambda name: None)
    return types.SimpleNamespace(sdk_launch=sdk_launch,
                                 set_restarting=set_restarting,
                                 set_backoff_pending=set_backoff_pending)


@pytest.mark.asyncio
async def test_launch_parks_and_reattaches_without_teardown(monkeypatch):
    """A parked launch request releases the slot and re-attaches on resume."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    slot_free_while_parked = asyncio.Event()

    async def fake_wait_for_parked_request(request_id):
        # While parked, the job must not hold a launch slot.
        if executor.job_id not in executor.starting:
            slot_free_while_parked.set()
        return request_id

    executor._wait_for_parked_request = mock.AsyncMock(
        side_effect=fake_wait_for_parked_request)
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        recovery_strategy._LaunchRequestParked(
            'req-123', 'Workload is pending on queue foo.'),
        None,
    ])

    result = await executor._launch(max_retry=1, raise_on_failure=True)

    assert result == 123.45
    # Only one sky.launch was submitted (and one stream started); the second
    # attempt re-attached to the same request and the SAME carried stream.
    assert patches.sdk_launch.call_count == 1
    assert executor._start_stream_task.call_count == 1
    stream_fut = executor._stream_futures[0]
    assert executor._await_launch_request.await_args_list == [
        mock.call('req-123', stream_fut),
        mock.call('req-123', stream_fut),
    ]
    # The carried stream must not have been cancelled by the park.
    assert not stream_fut.cancelled()
    # The launch slot was released while parked.
    assert slot_free_while_parked.is_set()
    # The task was set back to PENDING with the park reason while parked.
    patches.set_backoff_pending.assert_awaited_once()
    assert ('Workload is pending on queue foo.'
            in patches.set_backoff_pending.await_args.kwargs['reason'])
    # The task was set back to STARTING on resume.
    patches.set_restarting.assert_awaited_once_with(1, 0, False)
    # Parking must NOT tear down the (partially provisioned) cluster.
    executor._cleanup_cluster.assert_not_called()


@pytest.mark.asyncio
async def test_launch_parking_does_not_consume_retry_budget(monkeypatch):
    """Park cycles must not count against max_retry."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    executor._wait_for_parked_request = mock.AsyncMock(return_value='req-123')
    # One park followed by two real failures, with max_retry=2: the park must
    # not consume a retry, so both real failures should be attempted before
    # giving up.
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        recovery_strategy._LaunchRequestParked('req-123', 'pending'),
        RuntimeError('boom'),
        RuntimeError('boom'),
    ])

    with pytest.raises(exceptions.ManagedJobReachedMaxRetriesError):
        await executor._launch(max_retry=2, raise_on_failure=True)

    assert executor._await_launch_request.await_count == 3
    # The first attempt launched, the reattach reused the request and stream,
    # and the third attempt launched fresh (the failure tore the cluster
    # down), starting a new stream.
    assert patches.sdk_launch.call_count == 2
    assert executor._start_stream_task.call_count == 2


@pytest.mark.asyncio
async def test_launch_relaunches_when_parked_request_vanishes(monkeypatch):
    """If the parked request disappears, a fresh launch attempt is made."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    # Request vanishes while parked.
    executor._wait_for_parked_request = mock.AsyncMock(return_value=None)
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        recovery_strategy._LaunchRequestParked('req-123', 'pending'),
        None,
    ])

    result = await executor._launch(max_retry=1, raise_on_failure=True)

    assert result == 123.45
    # A fresh sky.launch (and a fresh stream) was submitted for the second
    # attempt; the orphaned first stream was not reused.
    assert patches.sdk_launch.call_count == 2
    assert executor._start_stream_task.call_count == 2
    assert executor._await_launch_request.await_args_list[1] == mock.call(
        'req-123', executor._stream_futures[1])
    # No teardown happened on the park path.
    executor._cleanup_cluster.assert_not_called()
    # Fix 1: the task was set PENDING while parked (see set_backoff_pending
    # above), including on the vanish path, so it must be restored to
    # STARTING/RECOVERING before the fresh launch attempt - even though this
    # is a "first attempt" from retry_cnt's perspective (retry_cnt == 1,
    # since a park does not consume a retry) and there is no
    # reattach_request_id to gate on (the request vanished).
    patches.set_restarting.assert_awaited_once_with(1, 0, False)


@pytest.mark.asyncio
async def test_launch_cancel_while_parked_cancels_request(monkeypatch):
    """Cancelling the job while parked cancels the outstanding request."""
    executor = _make_launch_executor()
    _patch_launch_environment(monkeypatch)
    executor._cancel_launch_request = mock.AsyncMock()

    parked = asyncio.Event()

    async def wait_forever(request_id):
        parked.set()
        await asyncio.Event().wait()  # Block until cancelled.

    executor._wait_for_parked_request = mock.AsyncMock(side_effect=wait_forever)
    executor._await_launch_request = mock.AsyncMock(
        side_effect=recovery_strategy._LaunchRequestParked(
            'req-123', 'pending'))

    task = asyncio.create_task(
        executor._launch(max_retry=1, raise_on_failure=True))
    await asyncio.wait_for(parked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    executor._cancel_launch_request.assert_awaited_once_with('req-123')


def test_start_stream_task_uses_context_preserving_executor(monkeypatch):
    """The stream must run with a copy of the caller's contextvars context
    (fix 2): loop.run_in_executor does not do this, unlike the
    asyncio.to_thread it replaced, so without it the per-job log
    redirection (which is contextvars-based) would be bypassed and logs
    would leak into the shared controller log instead of the per-job log."""
    executor = _make_bare_executor()

    to_thread_with_executor = mock.MagicMock(return_value='the-future')
    monkeypatch.setattr(recovery_strategy.context_utils,
                        'to_thread_with_executor', to_thread_with_executor)
    stream_and_get = mock.MagicMock(return_value='stream-result')
    monkeypatch.setattr(recovery_strategy.sdk, 'stream_and_get', stream_and_get)

    result = executor._start_stream_task('req-1')

    # It must go through the context-preserving helper (with our dedicated
    # bounded stream executor), not loop.run_in_executor / asyncio.to_thread.
    assert result == 'the-future'
    called_executor, called_fn = to_thread_with_executor.call_args.args
    assert called_executor is recovery_strategy._LAUNCH_STREAM_EXECUTOR
    # The submitted callable must invoke sdk.stream_and_get for this request
    # with the rich-status relay enabled.
    assert called_fn() == 'stream-result'
    stream_and_get.assert_called_once_with('req-1', relay_rich_status=True)


@pytest.mark.asyncio
async def test_launch_reattach_replaces_dead_stream_when_request_still_live(
        monkeypatch):
    """Fix 3: if the carried stream already failed (e.g. a transient
    transport error while parked) but the request itself is still live on
    the server, start a fresh stream instead of trusting the stale error -
    which would otherwise tear down an otherwise-healthy cluster."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    async def fake_wait_for_parked_request(request_id):
        # The carried stream dies while parked (e.g. the API server was
        # briefly unreachable), even though the request itself resumed
        # successfully.
        executor._stream_futures[0].set_exception(
            RuntimeError('stream transport dropped'))
        return request_id

    executor._wait_for_parked_request = mock.AsyncMock(
        side_effect=fake_wait_for_parked_request)
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        recovery_strategy._LaunchRequestParked('req-123', 'pending'),
        None,
    ])
    api_status = mock.MagicMock(return_value=[_request_payload('RUNNING')])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)

    result = await executor._launch(max_retry=1, raise_on_failure=True)

    assert result == 123.45
    # A replacement stream was started for the dead one; the original
    # sky.launch was not repeated (this is a reattach, not a fresh launch).
    assert patches.sdk_launch.call_count == 1
    assert executor._start_stream_task.call_count == 2
    assert executor._await_launch_request.await_args_list[1] == mock.call(
        'req-123', executor._stream_futures[1])
    executor._cleanup_cluster.assert_not_called()


@pytest.mark.asyncio
async def test_launch_reattach_surfaces_terminal_request_outcome(monkeypatch):
    """Fix 3: if the carried stream already failed and the request itself
    is terminal, surface the request's real outcome (here, its real
    failure) directly instead of waiting on a stream that will never
    produce one - and don't bother starting a replacement stream."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    async def fake_wait_for_parked_request(request_id):
        executor._stream_futures[0].set_exception(
            RuntimeError('stream transport dropped'))
        return request_id

    executor._wait_for_parked_request = mock.AsyncMock(
        side_effect=fake_wait_for_parked_request)
    executor._await_launch_request = mock.AsyncMock(
        side_effect=recovery_strategy._LaunchRequestParked(
            'req-123', 'pending'))
    api_status = mock.MagicMock(
        return_value=[_request_payload('FAILED', 'ran out of quota')])
    monkeypatch.setattr(recovery_strategy.sdk, 'api_status', api_status)
    sdk_get = mock.MagicMock(side_effect=ValueError('the real failure'))
    monkeypatch.setattr(recovery_strategy.sdk, 'get', sdk_get)

    with pytest.raises(exceptions.ManagedJobReachedMaxRetriesError):
        await executor._launch(max_retry=1, raise_on_failure=True)

    # The request's real (terminal) outcome was fetched directly - no
    # replacement stream, and _await_launch_request was not called again,
    # since the outcome was already decided.
    sdk_get.assert_called_once_with('req-123')
    assert executor._start_stream_task.call_count == 1
    assert executor._await_launch_request.await_count == 1
    # Unlike the parked/vanished paths, a genuine failure tears the
    # partially-provisioned cluster down.
    executor._cleanup_cluster.assert_called_once()


@pytest.mark.asyncio
async def test_launch_exception_before_reattach_cancels_parked_request(
        monkeypatch):
    """Fix 4: an exception raised after resuming from a park but before the
    inner try/except takes ownership of the request (e.g. a
    ManagedJobStatusError out of set_restarting_async) must not leak the
    parked request - it is best-effort cancelled here - and the exception
    must still propagate unchanged."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._cancel_launch_request = mock.AsyncMock()

    executor._wait_for_parked_request = mock.AsyncMock(return_value='req-123')
    executor._await_launch_request = mock.AsyncMock(
        side_effect=recovery_strategy._LaunchRequestParked(
            'req-123', 'pending'))
    patches.set_restarting.side_effect = exceptions.ManagedJobStatusError(
        'unexpected task status')

    with pytest.raises(exceptions.ManagedJobStatusError):
        await executor._launch(max_retry=1, raise_on_failure=True)

    executor._cancel_launch_request.assert_awaited_once_with('req-123')
    # The inner try/except (which would submit a second sky.launch or
    # reattach) never got to run again.
    assert patches.sdk_launch.call_count == 1
    assert executor._start_stream_task.call_count == 1


@pytest.mark.asyncio
async def test_launch_cancels_parked_request_when_poll_persistently_fails(
        monkeypatch):
    """Fix 5 + fix 4 together: when _wait_for_parked_request gives up
    because the status poll is persistently failing, it raises (fix 5)
    rather than falling back to a fresh launch attempt; _launch's generic
    exception handler (fix 4) then best-effort cancels the still-parked
    request and lets the error propagate."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._cancel_launch_request = mock.AsyncMock()

    executor._wait_for_parked_request = mock.AsyncMock(
        side_effect=RuntimeError('server unreachable'))
    executor._await_launch_request = mock.AsyncMock(
        side_effect=recovery_strategy._LaunchRequestParked(
            'req-123', 'pending'))

    with pytest.raises(RuntimeError, match='server unreachable'):
        await executor._launch(max_retry=1, raise_on_failure=True)

    executor._cancel_launch_request.assert_awaited_once_with('req-123')
    assert patches.sdk_launch.call_count == 1


@pytest.mark.asyncio
async def test_cancel_launch_request_tolerates_api_cancel_failure(monkeypatch):
    """Fix 6: sdk.api_cancel failing (e.g. the server is unreachable) must
    not raise - callers such as the asyncio.CancelledError handler in
    _launch rely on this being best-effort, so that this unrelated failure
    does not replace an in-flight cancellation."""
    executor = _make_bare_executor()
    monkeypatch.setattr(
        recovery_strategy.sdk, 'api_cancel',
        mock.MagicMock(side_effect=RuntimeError('server unreachable')))
    sdk_get = mock.MagicMock()
    monkeypatch.setattr(recovery_strategy.sdk, 'get', sdk_get)

    # Must not raise.
    await executor._cancel_launch_request('req-1')

    sdk_get.assert_not_called()


# ---------------------------------------------------------------------------
# Launch-retry classification: a bounded kind on the backoff event, so an
# error the launch path swallows is still counted somewhere.
# ---------------------------------------------------------------------------


def _roundtrip(exc):
    """Send an exception through the API server boundary and back.

    Errors reaching the broad except in _launch have almost always been
    serialized on the server and rebuilt on the controller, which is what
    makes type(e).__name__ the wrong key.
    """
    return exceptions.deserialize_exception(exceptions.serialize_exception(exc))


def test_error_kind_keeps_builtin_and_sky_classes():
    rebuilt = _roundtrip(ValueError('x'))
    assert recovery_strategy._error_kind(rebuilt) == 'ValueError'
    assert recovery_strategy._error_kind(
        _roundtrip(FileNotFoundError('Blob not found'))) == 'FileNotFoundError'


def test_error_kind_unwraps_cloud_error():
    """A cloud SDK class survives only inside CloudError.error_type."""

    class ApiException(Exception):
        pass

    ApiException.__module__ = 'kubernetes.client.exceptions'
    rebuilt = _roundtrip(ApiException('webhook unavailable'))
    # Precondition: the plain class name really is lost at the boundary.
    assert type(rebuilt).__name__ == 'CloudError'
    assert recovery_strategy._error_kind(rebuilt) == 'kubernetes:ApiException'


def test_error_kind_recovers_opaque_prefix():
    """A sky.* class outside sky/exceptions.py arrives as a bare Exception."""
    from sky.provision import common as provision_common
    rebuilt = _roundtrip(provision_common.StopFailoverError('boom'))
    assert type(rebuilt) is Exception
    assert recovery_strategy._error_kind(rebuilt) == 'opaque:StopFailoverError'


def test_error_kind_does_not_fabricate_class_names():
    """A plain `raise Exception(msg)` round-trips with its message intact.

    It is indistinguishable from the deserializer's prefixed fallback, so a
    colon alone would mint label values for classes that never existed.
    """
    rebuilt = _roundtrip(Exception('Unauthorized: token expired'))
    assert recovery_strategy._error_kind(rebuilt) == 'opaque:unparsed'


def test_error_kind_rejects_unbounded_prefix():
    assert recovery_strategy._error_kind(
        Exception('Failed to locate a cidr block')) == 'opaque:unparsed'
    # An arbitrary server payload must never become a label value.
    assert recovery_strategy._error_kind(
        Exception('{"error": "quota exceeded"}')) == 'opaque:unparsed'


def test_every_backoff_pending_call_site_passes_code():
    """The primary control: no path may reach the backoff without a code.

    Static, so it does not depend on any data existing -- unlike a NULL-code
    row, which for 30 days cannot be told apart from a pre-deploy row.
    """
    tree = ast.parse(inspect.getsource(recovery_strategy))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'set_backoff_pending_async'
    ]
    assert len(calls) == 2, 'call sites changed; update this test deliberately'
    for call in calls:
        assert any(kw.arg == 'code' for kw in call.keywords), (
            f'set_backoff_pending_async at line {call.lineno} omits code=')


def _codes(set_backoff_pending):
    """The code= passed to every backoff write, in order."""
    return [c.kwargs.get('code') for c in set_backoff_pending.call_args_list]


@pytest.mark.asyncio
async def test_launch_retry_code_for_unknown_exception(monkeypatch):
    """An unknown error reaches the backoff row instead of vanishing.

    max_retry=None is the initial-launch path, and the only one with the gap:
    recover() passes _MAX_RETRY_CNT, so it already escapes to the controller.
    """
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._await_launch_request = mock.AsyncMock(
        side_effect=[ValueError('Unable to start local API server'), None])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(patches.set_backoff_pending) == ['launch_retry:ValueError']


@pytest.mark.asyncio
async def test_launch_retry_code_for_job_submit_failure(monkeypatch):
    """The path that carries no exception at all is still attributed."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._await_launch_request = mock.AsyncMock(return_value=None)
    # First attempt launches but the job never starts; second one works.
    executor._wait_until_job_starts_on_cluster = mock.AsyncMock(
        side_effect=[(None, '_checks_exhausted'), (123.45, None)])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(patches.set_backoff_pending) == [
        'launch_retry:job_submit_failed_checks_exhausted'
    ]


@pytest.mark.asyncio
async def test_retry_code_resets_between_attempts(monkeypatch):
    """Attempt 2 must not inherit attempt 1's kind.

    The code is written in the NoClusterLaunchedError handler, long after the
    exception is gone, so without the per-iteration reset a later attempt that
    failed a different way reports the earlier attempt's class.
    """
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._await_launch_request = mock.AsyncMock(
        side_effect=[KeyError('status'), None, None])
    executor._wait_until_job_starts_on_cluster = mock.AsyncMock(
        side_effect=[(None, '_checks_exhausted'), (123.45, None)])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(patches.set_backoff_pending) == [
        'launch_retry:KeyError',
        'launch_retry:job_submit_failed_checks_exhausted',
    ]


@pytest.mark.asyncio
async def test_resources_unavailable_is_not_an_unknown_error(monkeypatch):
    """The expected path is coded distinctly from the unknowns."""
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    unavailable = exceptions.ResourcesUnavailableError('no capacity')
    # A non-empty failover history of the same type is what makes _launch
    # retry rather than fail the job at prechecks.
    unavailable.failover_history = [
        exceptions.ResourcesUnavailableError('zone a')
    ]
    executor._await_launch_request = mock.AsyncMock(
        side_effect=[unavailable, None])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(
        patches.set_backoff_pending) == ['launch_retry:resources_unavailable']


@pytest.mark.asyncio
async def test_pool_no_cluster_is_not_an_unknown_error(monkeypatch):
    """The commonest benign state must not read as an unknown launch error.

    It is raised inside the block the broad except guards, and pool jobs back
    off every second, so left to the class it would be the whole inventory.
    """
    executor = _make_launch_executor()
    executor.pool = 'my-pool'
    executor.job_id_on_pool_cluster = None
    patches = _patch_launch_environment(monkeypatch)
    monkeypatch.setattr(recovery_strategy.serve_utils, 'get_next_cluster_name',
                        lambda *a, **k: None)
    monkeypatch.setattr(recovery_strategy.state,
                        'set_job_id_on_pool_cluster',
                        mock.MagicMock(),
                        raising=False)

    # A pool with no capacity never finishes, so this one cannot assert on a
    # returned value like the others. Wait for the backoff write itself rather
    # than for a fixed duration: a fixed window makes the result depend on how
    # busy the machine is, and this test is the one the revert check leans on.
    task = asyncio.create_task(executor._launch(max_retry=None))
    for _ in range(500):
        if patches.set_backoff_pending.call_args_list:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await _cleanup_task(task)

    codes = _codes(patches.set_backoff_pending)
    assert codes, 'the pool job never reached the backoff'
    assert set(codes) == {'launch_retry:pool_no_cluster'}


@pytest.mark.asyncio
async def test_provision_no_cluster_launched_is_not_pool(monkeypatch):
    """The second arm: the same class from the provisioner is not benign.

    aws/config.py and azure/config.py raise NoClusterLaunchedError for real
    provisioning failures. A class-based handler would file those as
    pool_no_cluster and they would never escalate.
    """
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        exceptions.NoClusterLaunchedError('Failed to create security group'),
        None,
    ])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(
        patches.set_backoff_pending) == ['launch_retry:NoClusterLaunchedError']


@pytest.mark.asyncio
async def test_inventory_is_not_capped_when_the_metric_is(monkeypatch):
    """Why job_events.code stays even though the Counter exists.

    The metric's label budget folds rare kinds into 'other'. A rare kind is
    exactly what this instrumentation exists to surface, so the durable row
    must keep it by name.
    """
    from sky.metrics import utils as metrics_utils

    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)

    counter = mock.MagicMock()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    monkeypatch.setattr(metrics_utils, 'SKY_MANAGED_JOB_LAUNCH_RETRY_TOTAL',
                        counter)
    # Spend the whole budget on other kinds first.
    with metrics_utils._launch_retry_kinds_lock:
        saved = set(metrics_utils._launch_retry_kinds)
        metrics_utils._launch_retry_kinds.clear()
        metrics_utils._launch_retry_kinds.update(
            f'aws:Filler{i}'
            for i in range(metrics_utils._MAX_LAUNCH_RETRY_KINDS))
    try:
        rare = _roundtrip(_rare_cloud_exception())
        executor._await_launch_request = mock.AsyncMock(
            side_effect=[rare, None])
        assert await executor._launch(max_retry=None) == 123.45

        # The metric folded it away...
        assert counter.labels.call_args.kwargs == {
            'kind': metrics_utils.LAUNCH_RETRY_KIND_OTHER
        }
        # ...and the inventory kept it, by name.
        assert _codes(patches.set_backoff_pending) == [
            'launch_retry:kubernetes:RareApiException'
        ]
    finally:
        with metrics_utils._launch_retry_kinds_lock:
            metrics_utils._launch_retry_kinds.clear()
            metrics_utils._launch_retry_kinds.update(saved)


def _rare_cloud_exception():

    class RareApiException(Exception):
        pass

    RareApiException.__module__ = 'kubernetes.client.exceptions'
    return RareApiException('a kind never seen before')


@pytest.mark.asyncio
async def test_parked_path_is_coded_but_not_counted(monkeypatch):
    """Parking is not a launch retry; counting it would blur the metric."""
    from sky.metrics import utils as metrics_utils

    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    counter = mock.MagicMock()
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    monkeypatch.setattr(metrics_utils, 'SKY_MANAGED_JOB_LAUNCH_RETRY_TOTAL',
                        counter)

    executor._wait_for_parked_request = mock.AsyncMock(
        side_effect=lambda request_id: request_id)
    executor._await_launch_request = mock.AsyncMock(side_effect=[
        recovery_strategy._LaunchRequestParked('req-123',
                                               'Waiting on queue foo.'),
        None,
    ])

    assert await executor._launch(max_retry=None) == 123.45
    assert _codes(patches.set_backoff_pending) == ['launch_parked']
    counter.labels.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_max_retry_escalates_instead_of_coding(monkeypatch):
    """The gap is initial-launch-only, which the commit message claims.

    recover() passes _MAX_RETRY_CNT, so the same failure that loops forever on
    the initial launch exhausts and reaches the controller instead. Asserted
    with a small max_retry: the real 240 is about four hours of backoff.
    """
    executor = _make_launch_executor()
    patches = _patch_launch_environment(monkeypatch)
    executor._await_launch_request = mock.AsyncMock(
        side_effect=ValueError('Unable to start local API server'))

    with pytest.raises(exceptions.ManagedJobReachedMaxRetriesError):
        await executor._launch(max_retry=2, raise_on_failure=True)

    # One backoff row for the attempt that retried; the attempt that exhausted
    # the budget escalates rather than being absorbed and coded.
    assert _codes(patches.set_backoff_pending) == ['launch_retry:ValueError']


# ---------------------------------------------------------------------------
# Attributing the give-up reason of the job-submit wait. Without it the whole
# family is one bucket: three swallowed exceptions and two quiet exits all
# reach the caller as a bare None.
# ---------------------------------------------------------------------------


def _make_submit_wait_executor(monkeypatch):
    """An executor whose job-submit wait runs for real, with a live cluster."""
    executor = _make_launch_executor()
    del executor._wait_until_job_starts_on_cluster  # run the real one
    executor.job_id_on_pool_cluster = None
    executor.backend = mock.MagicMock()
    monkeypatch.setattr(recovery_strategy, 'MAX_JOB_CHECKING_RETRY', 2)
    monkeypatch.setattr(recovery_strategy.managed_job_utils,
                        'JOB_STARTED_STATUS_CHECK_GAP_SECONDS', 0)
    monkeypatch.setattr(
        recovery_strategy.backend_utils, 'refresh_cluster_status_handle',
        lambda *a, **k: (recovery_strategy.status_lib.ClusterStatus.UP, None))
    return executor


@pytest.mark.asyncio
async def test_submit_wait_attributes_cluster_preemption(monkeypatch):
    """The audit's 'preempted during provisioning' family gets its own name.

    It leaves the loop by `break`, not by an exception, so nothing downstream
    could tell it apart from a timeout without this.
    """
    executor = _make_submit_wait_executor(monkeypatch)
    monkeypatch.setattr(
        recovery_strategy.backend_utils, 'refresh_cluster_status_handle',
        lambda *a, **k:
        (recovery_strategy.status_lib.ClusterStatus.STOPPED, None))

    assert await executor._wait_until_job_starts_on_cluster() == (
        None, '_cluster_preempted')


def _break_cluster_status(monkeypatch):

    def boom(*args, **kwargs):
        raise ValueError('Unable to start local API server')

    monkeypatch.setattr(recovery_strategy.backend_utils,
                        'refresh_cluster_status_handle', boom)


def _break_job_status(monkeypatch):

    async def boom(*args, **kwargs):
        raise ValueError('Unable to start local API server')

    monkeypatch.setattr(recovery_strategy.managed_job_utils, 'get_job_status',
                        boom)


def _break_job_timestamp(monkeypatch):
    """Get past the status check so the timestamp fetch is reached."""

    async def running(*args, **kwargs):
        return recovery_strategy.job_lib.JobStatus.RUNNING, None

    def boom(*args, **kwargs):
        raise ValueError('Unable to start local API server')

    monkeypatch.setattr(recovery_strategy.managed_job_utils, 'get_job_status',
                        running)
    monkeypatch.setattr(recovery_strategy.managed_job_runtime, 'is_registered',
                        lambda: False)
    monkeypatch.setattr(recovery_strategy.managed_job_utils,
                        'get_job_timestamp', boom)


@pytest.mark.parametrize(
    'break_site',
    [_break_cluster_status, _break_job_status, _break_job_timestamp])
@pytest.mark.asyncio
async def test_submit_wait_attributes_a_swallowed_exception(
        monkeypatch, break_site):
    """A swallowed exception reaches the caller as its kind, not as None.

    Parametrised over all three sites on purpose: the same assignment is
    written three times, and a single-site test leaves two of them free to
    be deleted without anything going red.
    """
    executor = _make_submit_wait_executor(monkeypatch)
    break_site(monkeypatch)

    assert await executor._wait_until_job_starts_on_cluster() == (None,
                                                                  ':ValueError')


@pytest.mark.asyncio
async def test_submit_wait_attributes_a_transient_status(monkeypatch):
    """get_job_status can report a transient reason without raising."""
    executor = _make_submit_wait_executor(monkeypatch)

    async def transient(*args, **kwargs):
        return None, 'pod not found'

    monkeypatch.setattr(recovery_strategy.managed_job_utils, 'get_job_status',
                        transient)

    assert await executor._wait_until_job_starts_on_cluster() == (
        None, '_status_transient')


@pytest.mark.asyncio
async def test_submit_wait_falls_back_to_checks_exhausted(monkeypatch):
    """Running out of checks is its own reason, distinct from a NULL code."""
    executor = _make_submit_wait_executor(monkeypatch)

    async def still_init(*args, **kwargs):
        return recovery_strategy.job_lib.JobStatus.INIT, None

    monkeypatch.setattr(recovery_strategy.managed_job_utils, 'get_job_status',
                        still_init)

    assert await executor._wait_until_job_starts_on_cluster() == (
        None, '_checks_exhausted')


@pytest.mark.asyncio
async def test_submit_wait_clears_a_stale_anomaly(monkeypatch):
    """A clean poll means an earlier blip is not why the wait ended.

    Without clearing, a first-poll exception would be reported for a wait
    that actually timed out with the job still INIT -- attributing the retry
    to an error that had already resolved.
    """
    executor = _make_submit_wait_executor(monkeypatch)
    calls = {'n': 0}

    async def flaky_then_init(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise ValueError('a blip on the first poll')
        return recovery_strategy.job_lib.JobStatus.INIT, None

    monkeypatch.setattr(recovery_strategy.managed_job_utils, 'get_job_status',
                        flaky_then_init)
    monkeypatch.setattr(recovery_strategy, 'MAX_JOB_CHECKING_RETRY', 3)

    assert await executor._wait_until_job_starts_on_cluster() == (
        None, '_checks_exhausted')


def test_fixed_submit_reasons_are_exempt_from_the_metric_cap():
    """Saturate the budget, then check the fixed reasons still come back whole.

    Asserted by calling the cap, not by counting colons in the composed
    string: the rule is "any colon means open", so a colon-count assertion
    passes for spellings the cap still charges. That is how the first attempt
    at this fix shipped broken with a green test.
    """
    from sky.metrics import utils as metrics_utils

    with metrics_utils._launch_retry_kinds_lock:
        saved = set(metrics_utils._launch_retry_kinds)
        metrics_utils._launch_retry_kinds.clear()
        metrics_utils._launch_retry_kinds.update(
            f'aws:Filler{i}'
            for i in range(metrics_utils._MAX_LAUNCH_RETRY_KINDS))
    try:
        for reason in (recovery_strategy._SUBMIT_CLUSTER_PREEMPTED,
                       recovery_strategy._SUBMIT_STATUS_TRANSIENT,
                       recovery_strategy._SUBMIT_CHECKS_EXHAUSTED):
            kind = f'{recovery_strategy._KIND_JOB_SUBMIT_FAILED}{reason}'
            assert metrics_utils._capped_launch_retry_kind(kind) == kind, kind
        # An exception-derived reason is open, so a full budget does fold it.
        open_kind = (f'{recovery_strategy._KIND_JOB_SUBMIT_FAILED}'
                     f':kubernetes:ApiException')
        assert metrics_utils._capped_launch_retry_kind(
            open_kind) == metrics_utils.LAUNCH_RETRY_KIND_OTHER
    finally:
        with metrics_utils._launch_retry_kinds_lock:
            metrics_utils._launch_retry_kinds.clear()
            metrics_utils._launch_retry_kinds.update(saved)
