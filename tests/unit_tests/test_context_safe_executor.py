"""Tests for ContextSafeThreadPoolExecutor.

Worker threads start with an empty contextvars context, so request-scoped
state (the per-request SkyPilotContext from @context.contextual, the
current user, contextual env overrides) resolves to process defaults
inside a plain ThreadPoolExecutor. ContextSafeThreadPoolExecutor copies
the submitter's context into each task.
"""
import concurrent.futures
import contextvars
import threading

from sky.utils import context
from sky.utils import context_utils

_VAR: contextvars.ContextVar = contextvars.ContextVar('test_var',
                                                      default='DEFAULT')


def test_submit_runs_in_submitter_context():
    token = _VAR.set('caller-value')
    try:
        with context_utils.ContextSafeThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_VAR.get).result() == 'caller-value'
    finally:
        _VAR.reset(token)


def test_plain_executor_loses_context():
    """Documents the failure mode this class exists for."""
    token = _VAR.set('caller-value')
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_VAR.get).result() == 'DEFAULT'
    finally:
        _VAR.reset(token)


def test_context_is_copied_at_submit_time():
    """Mutations on the submitting thread after submit() are not visible."""
    release = threading.Event()

    def read_after_release():
        release.wait(5)
        return _VAR.get()

    token = _VAR.set('value-at-submit')
    try:
        with context_utils.ContextSafeThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(read_after_release)
            _VAR.set('value-after-submit')
            release.set()
            assert fut.result() == 'value-at-submit'
    finally:
        _VAR.reset(token)


def test_map_propagates_context():
    token = _VAR.set('mapped-value')
    try:
        with context_utils.ContextSafeThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: _VAR.get(), range(4)))
        assert results == ['mapped-value'] * 4
    finally:
        _VAR.reset(token)


def test_skypilot_context_propagates():
    """The per-request SkyPilotContext travels into the worker."""
    ctx = context.initialize()
    assert context.get() is ctx
    with context_utils.ContextSafeThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(context.get).result() is ctx
        # A plain executor would see no context at all in the worker.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as plain:
            assert plain.submit(context.get).result() is None
