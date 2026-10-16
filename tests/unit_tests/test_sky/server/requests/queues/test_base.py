"""Tests for queue backends' queue-wait metric.

The queue-wait metric must measure time between *enqueue* and *dequeue*, not
time since request creation. These tests pin that contract for the in-memory
``LocalQueueBackend``: the dequeued item is returned unchanged, and the wait
recorded is measured from the (latest) ``put()`` — so a re-enqueue resets the
reference and excludes any time the request spent paused or running.
"""

import socket
import time
from unittest import mock

import pytest

from sky.server.requests.queues import base


def _patch_metric(monkeypatch):
    """Enable metrics and swap in a mock histogram; return the mock."""
    monkeypatch.setattr(base.metrics_utils, 'METRICS_ENABLED', True)
    mock_hist = mock.MagicMock()
    monkeypatch.setattr(base.metrics_utils, 'SKY_APISERVER_QUEUE_WAIT_SECONDS',
                        mock_hist)
    return mock_hist


def test_local_backend_roundtrip_preserves_item(monkeypatch):
    _patch_metric(monkeypatch)
    backend = base.LocalQueueBackend('long')

    item = ('req-1', False, True)
    backend.put(item)
    assert backend.get() == item
    # Empty queue returns None without emitting.
    assert backend.get() is None


def test_local_backend_observes_wait_since_enqueue(monkeypatch):
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


def test_local_backend_requeue_resets_wait(monkeypatch):
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


def test_local_backend_no_emit_when_metrics_disabled(monkeypatch):
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


def test_mp_backend_roundtrip_and_observes_wait(mp_backend, monkeypatch):
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


def test_mp_backend_requeue_resets_wait(mp_backend, monkeypatch):
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
