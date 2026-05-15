"""Tests for SkyPilotContext cancel-callback channel.

The cancel callback channel lets blocking sync work (e.g. a gRPC streaming
iterator stuck in Condition.wait inside __next__) react to ctx.cancel()
even though it cannot poll is_canceled() itself.
"""
import threading

from sky.utils import context


def test_callback_fires_on_cancel():
    ctx = context.SkyPilotContext()
    calls = []
    ctx.register_cancel_callback(lambda: calls.append('a'))
    ctx.register_cancel_callback(lambda: calls.append('b'))
    assert not calls
    ctx.cancel()
    assert calls == ['a', 'b']
    assert ctx.is_canceled()


def test_cancel_is_idempotent():
    ctx = context.SkyPilotContext()
    calls = []
    ctx.register_cancel_callback(lambda: calls.append(1))
    ctx.cancel()
    ctx.cancel()
    ctx.cancel()
    assert calls == [1]


def test_register_after_cancel_fires_immediately():
    ctx = context.SkyPilotContext()
    ctx.cancel()
    calls = []
    ctx.register_cancel_callback(lambda: calls.append('late'))
    assert calls == ['late']


def test_unregister_prevents_firing():
    ctx = context.SkyPilotContext()
    calls = []

    def cb():
        calls.append('fired')

    ctx.register_cancel_callback(cb)
    ctx.unregister_cancel_callback(cb)
    ctx.cancel()
    assert not calls


def test_unregister_unknown_callback_is_noop():
    ctx = context.SkyPilotContext()
    ctx.unregister_cancel_callback(lambda: None)  # Should not raise.


def test_callback_exception_does_not_block_others():
    ctx = context.SkyPilotContext()
    calls = []

    def boom():
        raise RuntimeError('intentional')

    ctx.register_cancel_callback(boom)
    ctx.register_cancel_callback(lambda: calls.append('after-boom'))
    ctx.cancel()
    assert calls == ['after-boom']
    assert ctx.is_canceled()


def test_register_from_other_thread():
    ctx = context.SkyPilotContext()
    calls = []
    ready = threading.Event()

    def worker():
        ctx.register_cancel_callback(lambda: calls.append('worker'))
        ready.set()

    t = threading.Thread(target=worker)
    t.start()
    ready.wait(timeout=1)
    t.join(timeout=1)
    ctx.cancel()
    assert calls == ['worker']


def test_concurrent_register_and_cancel_race():
    """register_cancel_callback racing with cancel() must never drop the
    callback: either it goes on the list and fires on cancel, or the
    register call sees is_set() and fires immediately. Never both, never
    neither."""

    def _race_once(idx):
        del idx
        ctx = context.SkyPilotContext()
        calls = []
        start = threading.Event()

        def canceller():
            start.wait()
            ctx.cancel()

        def registerer():
            start.wait()
            ctx.register_cancel_callback(lambda: calls.append('x'))

        threads = [
            threading.Thread(target=canceller),
            threading.Thread(target=registerer),
        ]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=2)
        assert calls == ['x']

    for i in range(50):
        _race_once(i)
