"""Unit tests for the cluster events recorded by core.down()/core.stop().
"""
import unittest.mock as mock

from sky import core
from sky import global_user_state
from sky import models
from sky.utils import common_utils
from sky.utils import status_lib


def _patch_down(monkeypatch, handle, backend, add_event):
    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: handle)
    monkeypatch.setattr(core.backend_utils, 'get_backend_from_handle',
                        lambda h: backend)
    monkeypatch.setattr(core.usage_lib,
                        'record_cluster_name_for_current_operation',
                        lambda name: None)
    monkeypatch.setattr(core, '_maybe_run_down_hooks',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(global_user_state, 'add_cluster_event', add_event)


def _patch_stop(monkeypatch, handle, backend, add_event):
    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: handle)
    monkeypatch.setattr(core.backend_utils, 'get_backend_from_handle',
                        lambda h: backend)
    monkeypatch.setattr(core.usage_lib,
                        'record_cluster_name_for_current_operation',
                        lambda name: None)
    monkeypatch.setattr(core, '_maybe_run_stop_hooks',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(global_user_state, 'add_cluster_event', add_event)


def _patch_request_context(monkeypatch, name='alice', user_hash='abcd1234'):
    """Make the call look like it runs inside a server-side API request."""
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: True)
    monkeypatch.setattr(common_utils, 'get_current_user',
                        lambda: models.User(id=user_hash, name=name))
    monkeypatch.setattr(common_utils, 'get_current_request_id', lambda: 'req-1')


def test_down_records_termination_event(monkeypatch):
    """A user-initiated core.down() must record a STATUS_CHANGE event before
    tearing down, so the cluster hash is still resolvable (the row is deleted
    during teardown)."""
    cluster_name = 'evt-test'
    handle = mock.MagicMock()
    backend = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_down(monkeypatch, handle, backend, add_event)

    # Track call order: the event must be recorded before teardown removes the
    # cluster row.
    order = []
    add_event.side_effect = lambda *args, **kwargs: order.append('event')
    backend.teardown.side_effect = lambda *args, **kwargs: order.append(
        'teardown')

    core.down(cluster_name, user_initiated=True)

    add_event.assert_called_once_with(
        cluster_name, None, 'Cluster was terminated by user.',
        global_user_state.ClusterEventType.STATUS_CHANGE)
    assert order == ['event', 'teardown']
    backend.teardown.assert_called_once_with(handle,
                                             terminate=True,
                                             purge=False)


def test_down_without_user_initiated_records_no_event(monkeypatch):
    """A non-user-initiated core.down() (e.g. autodown) must NOT record a
    'terminated by user' event, but must still tear the cluster down."""
    cluster_name = 'evt-test'
    handle = mock.MagicMock()
    backend = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_down(monkeypatch, handle, backend, add_event)

    core.down(cluster_name)

    add_event.assert_not_called()
    backend.teardown.assert_called_once_with(handle,
                                             terminate=True,
                                             purge=False)


def test_down_event_names_the_requesting_user(monkeypatch):
    """Run inside an API request, the event names the user and request."""
    handle = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_down(monkeypatch, handle, mock.MagicMock(), add_event)
    _patch_request_context(monkeypatch)

    core.down('evt-test', user_initiated=True)

    add_event.assert_called_once_with(
        'evt-test', None,
        'Cluster was terminated by user alice (request ID: req-1).',
        global_user_state.ClusterEventType.STATUS_CHANGE)


def test_down_event_falls_back_to_the_user_hash(monkeypatch):
    """With no display name, the event still identifies the requester."""
    handle = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_down(monkeypatch, handle, mock.MagicMock(), add_event)
    _patch_request_context(monkeypatch, name=None)

    core.down('evt-test', user_initiated=True)

    add_event.assert_called_once_with(
        'evt-test', None,
        'Cluster was terminated by user abcd1234 (request ID: req-1).',
        global_user_state.ClusterEventType.STATUS_CHANGE)


def test_stop_records_attributed_event(monkeypatch):
    """core.stop() records the same attribution before tearing down."""
    handle = mock.MagicMock()
    backend = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_stop(monkeypatch, handle, backend, add_event)
    _patch_request_context(monkeypatch)

    order = []
    add_event.side_effect = lambda *args, **kwargs: order.append('event')
    backend.teardown.side_effect = lambda *args, **kwargs: order.append(
        'teardown')

    core.stop('evt-test')

    add_event.assert_called_once_with(
        'evt-test', status_lib.ClusterStatus.STOPPED,
        'Cluster was stopped by user alice (request ID: req-1).',
        global_user_state.ClusterEventType.STATUS_CHANGE)
    assert order == ['event', 'teardown']
    backend.teardown.assert_called_once_with(handle,
                                             terminate=False,
                                             purge=False)


def test_stop_event_without_request_context(monkeypatch):
    """An in-process caller has nobody to name, so the text is unchanged."""
    handle = mock.MagicMock()
    add_event = mock.MagicMock()
    _patch_stop(monkeypatch, handle, mock.MagicMock(), add_event)
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: False)

    core.stop('evt-test')

    add_event.assert_called_once_with(
        'evt-test', status_lib.ClusterStatus.STOPPED,
        'Cluster was stopped by user.',
        global_user_state.ClusterEventType.STATUS_CHANGE)
