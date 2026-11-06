"""Tests for the queue backend factories and plugin hooks."""

import socket
import time
from unittest import mock

import pytest

from sky.server import plugins
from sky.server.requests.queues import base


def _patch_metric(monkeypatch):
    """Enable metrics and swap in a mock histogram; return the mock."""
    monkeypatch.setattr(base.metrics_utils, 'METRICS_ENABLED', True)
    mock_hist = mock.MagicMock()
    monkeypatch.setattr(base.metrics_utils, 'SKY_APISERVER_QUEUE_WAIT_SECONDS',
                        mock_hist)
    return mock_hist


def test_default_factory_is_the_database_queue(monkeypatch):
    monkeypatch.setattr(base, '_queue_backend_factory', None)

    assert isinstance(base.get_queue_backend_factory(), base.DbQueueFactory)
    assert base.DbQueueFactory().start() is None


def test_registered_factory_wins_over_the_default(monkeypatch):
    monkeypatch.setattr(base, '_queue_backend_factory', None)
    plugin_factory = mock.MagicMock(spec=base.QueueBackendFactory)

    base.set_queue_backend_factory(plugin_factory)

    assert base.get_queue_backend_factory() is plugin_factory


# The in-memory factories stay importable and usable as opt-in plugin
# backends.


def test_in_memory_local_backend_roundtrip_preserves_item(monkeypatch):
    _patch_metric(monkeypatch)
    backend = base.LocalQueueBackend('long')

    item = ('req-1', False, True)
    backend.put(item)
    assert backend.get() == item
    # Empty queue returns None without emitting.
    assert backend.get() is None


def test_in_memory_local_backend_observes_wait_since_enqueue(monkeypatch):
    mock_hist = _patch_metric(monkeypatch)
    backend = base.LocalQueueBackend('short')

    backend.put(('req-2', False, False))
    time.sleep(0.05)
    backend.get()

    mock_hist.labels.assert_called_once_with(schedule_type='short')
    (observed,), _ = mock_hist.labels.return_value.observe.call_args
    # Wait reflects the ~50ms the entry sat in the queue, not 0 and not a
    # request-creation timestamp.
    assert observed >= 0.04


def test_in_memory_local_backend_requeue_resets_wait(monkeypatch):
    """A re-enqueue measures only the new wait."""
    mock_hist = _patch_metric(monkeypatch)
    backend = base.LocalQueueBackend('long')

    item = ('req-3', False, True)
    # First enqueue sits a while, then is dequeued (simulating admit + run).
    backend.put(item)
    time.sleep(0.3)
    assert backend.get() == item
    # Re-enqueue and dequeue promptly: the second wait must be much smaller,
    # independent of how long the first attempt took. Compare relative to the
    # first wait rather than an absolute threshold to avoid CI flakiness.
    backend.put(item)
    backend.get()

    observed = [
        c[0][0] for c in mock_hist.labels.return_value.observe.call_args_list
    ]
    first_wait, second_wait = observed[0], observed[1]
    assert first_wait >= 0.25
    assert second_wait < first_wait / 2


def test_in_memory_local_backend_no_emit_when_metrics_disabled(monkeypatch):
    monkeypatch.setattr(base.metrics_utils, 'METRICS_ENABLED', False)
    mock_hist = mock.MagicMock()
    monkeypatch.setattr(base.metrics_utils, 'SKY_APISERVER_QUEUE_WAIT_SECONDS',
                        mock_hist)
    backend = base.LocalQueueBackend('long')

    backend.put(('req-4', False, False))
    assert backend.get() == ('req-4', False, False)
    mock_hist.labels.assert_not_called()


@pytest.fixture()
def mp_backend():
    """A MultiprocessingQueueBackend backed by a live manager on a free port.

    The cross-process backend adds pickling of the (item, enqueued_at) payload
    over the manager, so it exercises a path LocalQueueBackend can't. get() runs
    in this process, so metric patching via monkeypatch still applies.
    """
    sock = socket.socket()
    sock.bind(('localhost', 0))
    port = sock.getsockname()[1]
    sock.close()

    factory = base.MultiprocessingQueueFactory(port=port)
    process = factory.start()
    try:
        yield factory.create_queue('long')
    finally:
        factory.stop(process)


def test_in_memory_mp_backend_roundtrip_and_observes_wait(
        mp_backend, monkeypatch):
    mock_hist = _patch_metric(monkeypatch)

    item = ('req-5', False, True)
    mp_backend.put(item)
    time.sleep(0.05)
    # Item survives the pickle round-trip through the manager unchanged.
    assert mp_backend.get() == item

    mock_hist.labels.assert_called_once_with(schedule_type='long')
    (observed,), _ = mock_hist.labels.return_value.observe.call_args
    assert observed >= 0.04
    # Empty queue returns None without emitting again.
    assert mp_backend.get() is None
    mock_hist.labels.assert_called_once()


def test_in_memory_mp_backend_requeue_resets_wait(mp_backend, monkeypatch):
    mock_hist = _patch_metric(monkeypatch)

    item = ('req-6', False, True)
    mp_backend.put(item)
    time.sleep(0.3)
    assert mp_backend.get() == item
    mp_backend.put(item)
    assert mp_backend.get() == item

    observed = [
        c[0][0] for c in mock_hist.labels.return_value.observe.call_args_list
    ]
    first_wait, second_wait = observed[0], observed[1]
    assert first_wait >= 0.25
    assert second_wait < first_wait / 2


def _free_port():
    sock = socket.socket()
    sock.bind(('localhost', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_plugin_registers_multiprocessing_factory(monkeypatch):
    """A plugin that registers MultiprocessingQueueFactory(port) keeps working.

    A plugin that registers the factory to use its own port.
    """
    monkeypatch.setattr(base, '_queue_backend_factory', None)
    context = plugins.ExtensionContext(context=plugins.PluginContext.MAIN)
    context.register_queue_backend_factory(
        base.MultiprocessingQueueFactory(_free_port()))

    factory = base.get_queue_backend_factory()
    assert isinstance(factory, base.MultiprocessingQueueFactory)
    process = factory.start()
    try:
        queue = factory.create_queue('long')
        queue.put(('req-plugin-mp', False, True))
        assert queue.get() == ('req-plugin-mp', False, True)
        assert queue.get() is None
    finally:
        factory.stop(process)


def test_plugin_sets_local_factory(monkeypatch):
    """A caller of set_queue_backend_factory(LocalQueueFactory()) keeps working.

    A caller that sets the process-local factory, for example in a test.
    """
    monkeypatch.setattr(base, '_queue_backend_factory', None)
    base.set_queue_backend_factory(base.LocalQueueFactory())

    factory = base.get_queue_backend_factory()
    assert isinstance(factory, base.LocalQueueFactory)
    assert factory.start() is None
    queue = factory.create_queue('long')
    queue.put(('req-plugin-local', True, False))
    assert queue.get() == ('req-plugin-local', True, False)
    assert queue.get() is None


@pytest.mark.parametrize('factory_cls,args', [
    (base.LocalQueueFactory, ()),
    (base.MultiprocessingQueueFactory, (12345,)),
])
def test_deprecated_factory_warns(monkeypatch, factory_cls, args):
    warning = mock.MagicMock()
    monkeypatch.setattr(base.logger, 'warning', warning)

    first = factory_cls(*args)

    warning.assert_called_once()
    message = warning.call_args[0][0]
    assert factory_cls.__name__ in message
    assert 'deprecated' in message
    # Construction still succeeds; the compatibility tests above run the
    # queues themselves.
    assert isinstance(first, base.QueueBackendFactory)
