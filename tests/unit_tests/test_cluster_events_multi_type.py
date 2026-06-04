"""Tests for sky.core.get_cluster_events multi-type parsing.

`core.get_cluster_events` accepts a comma-separated event_type string (e.g.
'STATUS_CHANGE,LAUNCH_PROGRESS') and forwards the parsed list to the storage
reader, which does the actual merge/order/limit. The merge behavior itself is
covered by test_sky/test_global_user_state_cluster_events.py.
"""
import pytest

from sky import core
from sky import global_user_state


@pytest.mark.parametrize('event_type,expected_types', [
    ('STATUS_CHANGE', [global_user_state.ClusterEventType.STATUS_CHANGE]),
    ('STATUS_CHANGE,LAUNCH_PROGRESS', [
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    ]),
    ('STATUS_CHANGE, LAUNCH_PROGRESS ', [
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    ]),
])
def test_parses_and_forwards_event_types(monkeypatch, event_type,
                                         expected_types):
    captured = {}

    def _fake(event_type, include_timestamps, limit, **_kwargs):
        captured['event_type'] = event_type
        captured['include_timestamps'] = include_timestamps
        captured['limit'] = limit
        return ['sentinel']

    monkeypatch.setattr(global_user_state, 'get_cluster_events', _fake)

    result = core.get_cluster_events(cluster_hash='h',
                                     event_type=event_type,
                                     include_timestamps=True,
                                     limit=7)
    # The reader's result is returned unchanged (no merge in core).
    assert result == ['sentinel']
    # Types are parsed (and trimmed) into a list; flags forwarded as-is.
    assert captured['event_type'] == expected_types
    assert captured['include_timestamps'] is True
    assert captured['limit'] == 7
