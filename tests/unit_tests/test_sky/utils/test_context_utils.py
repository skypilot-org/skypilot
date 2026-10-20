"""Unit tests for sky.utils.context_utils module."""
import asyncio
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
