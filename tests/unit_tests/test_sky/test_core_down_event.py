"""Unit tests for the cluster event recorded by core.down()."""
import unittest.mock as mock

from sky import core
from sky import global_user_state


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
