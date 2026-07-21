"""Unit tests for sky.jobs.recovery_strategy helpers."""
import asyncio
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
        return_value=123.45)
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
