"""Unit tests for sky.utils.context_utils module."""
import asyncio
import contextvars
import threading
from typing import Optional, Union

import pytest

from sky.utils import context
from sky.utils import context_utils


@context_utils.cancellation_guard
def original_function(arg1: int, arg2: str) -> Optional[Union[int, str]]:
    return None


def _run_in_fresh_thread(func):
    """Run func in a new thread, which starts with no context set.

    Contextvars are per-thread, so a context initialized (and cancelled) inside
    func stays there: leaking a cancelled context into the test process would
    make cancellation_guard raise in every later test.
    """
    box = {}

    def target():
        # SkyPilotContext holds an asyncio.Event, whose constructor looks up the
        # thread's event loop on Python 3.9, so give the thread one to find.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box['value'] = func()
        except Exception as e:  # pylint: disable=broad-except
            box['error'] = e
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if 'error' in box:
        raise box['error']
    return box.get('value')


def test_cancellation_guard_perserves_typecheck():
    # Verify that the decorated function has the same signature
    assert original_function.__name__ == 'original_function'
    assert original_function.__annotations__ == {
        'arg1': int,
        'arg2': str,
        'return': Optional[Union[int, str]]
    }

    # Verify that the decorated function can be called with the same signature
    assert original_function(1, 'test') is None


def test_raise_if_canceled_without_context():
    """Outside a context there is nothing to cancel, so this is a no-op."""

    def body():
        assert context.get() is None
        context_utils.raise_if_canceled()

    _run_in_fresh_thread(body)


def test_raise_if_canceled_with_live_context():

    def body():
        context.initialize()
        assert context.get() is not None
        context_utils.raise_if_canceled()

    _run_in_fresh_thread(body)


def test_raise_if_canceled_after_cancel():

    def body():
        context.initialize()
        ctx = context.get()
        assert ctx is not None
        ctx.cancel()
        with pytest.raises(asyncio.CancelledError):
            context_utils.raise_if_canceled()

    _run_in_fresh_thread(body)


def test_raise_if_canceled_breaks_polling_loop():
    """A sleep-based polling loop exits within one iteration of a cancel."""

    def body():
        context.initialize()
        ctx = context.get()
        assert ctx is not None
        threading.Timer(0.1, ctx.cancel).start()
        iterations = 0
        with pytest.raises(asyncio.CancelledError):
            while True:
                context_utils.raise_if_canceled()
                iterations += 1
                assert iterations < 1000, 'loop did not observe cancellation'
                # Stand-in for the real loops' time.sleep(...).
                threading.Event().wait(0.05)

    _run_in_fresh_thread(body)


_SUBMITTER_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    'submitter_var', default='unset')


def test_context_propagating_thread_pool_runs_in_submitter_context():
    """Tasks see the submitter's context variables, per submit."""
    with context_utils.ContextPropagatingThreadPool(max_workers=2) as pool:
        futures = []
        for value in ('first', 'second'):
            token = _SUBMITTER_VAR.set(value)
            futures.append(pool.submit(_SUBMITTER_VAR.get))
            _SUBMITTER_VAR.reset(token)
        mapped = list(pool.map(lambda _: _SUBMITTER_VAR.get(), range(3)))
    assert [f.result() for f in futures] == ['first', 'second']
    assert mapped == ['unset'] * 3


def test_context_propagating_thread_pool_carries_request_context():
    """Tasks see the request's env overrides from sky.utils.context."""

    def in_request() -> Optional[str]:
        ctx = context.initialize()
        ctx.override_envs({'SKYPILOT_TEST_POOL_VAR': 'from-request'})
        with context_utils.ContextPropagatingThreadPool(max_workers=1) as pool:
            return pool.submit(lambda: context.get().env_overrides.get(
                'SKYPILOT_TEST_POOL_VAR')).result()

    # Run in a copied context so the initialized context does not leak.
    assert contextvars.copy_context().run(in_request) == 'from-request'
