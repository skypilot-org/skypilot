"""The half of the tripwire the mapping test cannot reach.

`test_every_event_type_has_a_retention_window` asserts the MAPPING covers the
enum. Nothing asserts the DAEMON reads the mapping. This runs one pass of the
daemon and compares the types it actually swept against `ClusterEventType` --
so the expectation comes from the enum and the actual comes from executing the
loop. Neither half can be re-implemented in the test, which is what made the
deleted version a tautology.

The loop is escaped through the daemon's own documented exit: it breaks on
asyncio.CancelledError, which the broad `except Exception` cannot swallow
because CancelledError is a BaseException. The last call of a pass raises it.
"""
import asyncio

import pytest

from sky import global_user_state as gus
from sky.utils import asyncio_utils


@pytest.mark.asyncio
async def test_the_daemon_sweeps_every_event_type(monkeypatch):
    swept = []

    async def _no_jitter(_name):
        return None

    def _record(hours, event_type):
        swept.append(event_type)

    def _stop_after_one_pass(_hours):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio_utils, 'sleep_startup_jitter', _no_jitter)
    monkeypatch.setattr(gus, 'cleanup_cluster_events_with_retention', _record)
    monkeypatch.setattr(gus, 'cleanup_launch_attempts_with_retention',
                        _stop_after_one_pass)

    await gus.cluster_event_retention_daemon()

    assert set(swept) == set(gus.ClusterEventType), (
        'the daemon did not sweep every event type; missing '
        f'{set(gus.ClusterEventType) - set(swept)}')
