"""Unit tests for the cluster event recorded by core.down()."""
import unittest.mock as mock

from sky import core
from sky import global_user_state


def test_down_records_termination_event(monkeypatch):
    """core.down() must record a STATUS_CHANGE event before tearing down, so the
    cluster hash is still resolvable (the row is deleted during teardown)."""
    cluster_name = 'evt-test'
    handle = mock.MagicMock()
    backend = mock.MagicMock()

    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: handle)
    monkeypatch.setattr(core.backend_utils, 'get_backend_from_handle',
                        lambda h: backend)
    monkeypatch.setattr(core.usage_lib,
                        'record_cluster_name_for_current_operation',
                        lambda name: None)
    monkeypatch.setattr(core, '_maybe_run_down_hooks',
                        lambda *args, **kwargs: None)

    add_event = mock.MagicMock()
    monkeypatch.setattr(global_user_state, 'add_cluster_event', add_event)

    # Track call order: the event must be recorded before teardown removes the
    # cluster row.
    order = []
    add_event.side_effect = lambda *args, **kwargs: order.append('event')
    backend.teardown.side_effect = lambda *args, **kwargs: order.append(
        'teardown')

    core.down(cluster_name)

    add_event.assert_called_once_with(
        cluster_name, None, 'Cluster was terminated by user.',
        global_user_state.ClusterEventType.STATUS_CHANGE)
    assert order == ['event', 'teardown']
    backend.teardown.assert_called_once_with(handle,
                                             terminate=True,
                                             purge=False)
