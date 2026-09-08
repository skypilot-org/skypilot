"""Tests for housekeeping daemon wake intervals and startup jitter."""
import asyncio
from unittest import mock

import pytest

from sky import global_user_state
from sky import skypilot_config
from sky.server.requests import requests as requests_lib
from sky.utils import asyncio_utils


class _StopLoop(Exception):
    """Raised from the patched sleep to break the daemon's `while True`."""


def _sleep_recorder(recorded):

    async def fake_sleep(secs):
        recorded.append(secs)
        raise _StopLoop

    return fake_sleep


@pytest.mark.asyncio
async def test_requests_gc_interval_is_independent_of_retention():
    """The wake interval must not scale with the retention window.

    A long retention window means rows are kept longer, not that eligible
    rows should be left to accumulate between passes.
    """
    slept = []
    # 30 days of retention: if the interval tracked retention, the daemon
    # would sleep for 30 days and each pass would have a month of rows.
    with mock.patch.object(skypilot_config, 'reload_config'), \
         mock.patch.object(skypilot_config, 'get_nested',
                           return_value=24 * 30), \
         mock.patch.object(requests_lib,
                           'clean_finished_requests_with_retention',
                           mock.AsyncMock()), \
         mock.patch.object(asyncio_utils, 'sleep_startup_jitter',
                           mock.AsyncMock()), \
         mock.patch.object(asyncio, 'sleep', _sleep_recorder(slept)):
        with pytest.raises(_StopLoop):
            await requests_lib.requests_gc_daemon()

    assert slept == [requests_lib._REQUESTS_GC_INTERVAL_SECONDS]
    assert requests_lib._REQUESTS_GC_INTERVAL_SECONDS == 3600


@pytest.mark.asyncio
async def test_requests_gc_interval_same_for_short_retention():
    """A short retention window does not shorten the interval either."""
    slept = []
    with mock.patch.object(skypilot_config, 'reload_config'), \
         mock.patch.object(skypilot_config, 'get_nested', return_value=1), \
         mock.patch.object(requests_lib,
                           'clean_finished_requests_with_retention',
                           mock.AsyncMock()), \
         mock.patch.object(asyncio_utils, 'sleep_startup_jitter',
                           mock.AsyncMock()), \
         mock.patch.object(asyncio, 'sleep', _sleep_recorder(slept)):
        with pytest.raises(_StopLoop):
            await requests_lib.requests_gc_daemon()

    assert slept == [requests_lib._REQUESTS_GC_INTERVAL_SECONDS]


@pytest.mark.asyncio
async def test_cluster_event_interval_is_independent_of_retention():
    """Same for the cluster-event retention daemon.

    Its default retention is 30 days, so an interval derived from retention
    left it waking roughly once a month.
    """
    slept = []
    with mock.patch.object(skypilot_config, 'reload_config'), \
         mock.patch.object(skypilot_config, 'get_nested',
                           return_value=24 * 30), \
         mock.patch.object(global_user_state,
                           'cleanup_cluster_events_with_retention'), \
         mock.patch.object(asyncio_utils, 'sleep_startup_jitter',
                           mock.AsyncMock()), \
         mock.patch.object(asyncio, 'sleep', _sleep_recorder(slept)):
        with pytest.raises(_StopLoop):
            await global_user_state.cluster_event_retention_daemon()

    assert slept == [global_user_state.CLUSTER_EVENT_DAEMON_INTERVAL_SECONDS]
    assert global_user_state.CLUSTER_EVENT_DAEMON_INTERVAL_SECONDS == 3600


@pytest.mark.asyncio
async def test_startup_jitter_bounded_and_random():
    """sleep_startup_jitter sleeps within [0, max) and varies between calls."""
    slept = []

    async def fake_sleep(secs):
        slept.append(secs)

    with mock.patch.object(asyncio, 'sleep', fake_sleep):
        for _ in range(50):
            await asyncio_utils.sleep_startup_jitter('t', max_seconds=100)

    assert len(slept) == 50
    assert all(0 <= s < 100 for s in slept), slept
    # Identical values across 50 draws would mean it is not random.
    assert len(set(slept)) > 40, 'delays are not spread out'


@pytest.mark.asyncio
async def test_startup_jitter_disabled_does_not_sleep():
    slept = []

    async def fake_sleep(secs):
        slept.append(secs)

    with mock.patch.object(asyncio, 'sleep', fake_sleep):
        await asyncio_utils.sleep_startup_jitter('t', max_seconds=0)
        await asyncio_utils.sleep_startup_jitter('t', max_seconds=-1)

    assert not slept


@pytest.mark.asyncio
async def test_gc_daemon_jitters_before_first_pass():
    """The jitter runs before the loop, not between passes."""
    order = []

    async def fake_jitter(*_args, **_kwargs):
        order.append('jitter')

    async def fake_clean(*_args, **_kwargs):
        order.append('pass')

    async def fake_sleep(_secs):
        order.append('interval')
        raise _StopLoop

    with mock.patch.object(skypilot_config, 'reload_config'), \
         mock.patch.object(skypilot_config, 'get_nested', return_value=24), \
         mock.patch.object(requests_lib,
                           'clean_finished_requests_with_retention',
                           fake_clean), \
         mock.patch.object(asyncio_utils, 'sleep_startup_jitter',
                           fake_jitter), \
         mock.patch.object(asyncio, 'sleep', fake_sleep):
        with pytest.raises(_StopLoop):
            await requests_lib.requests_gc_daemon()

    assert order == ['jitter', 'pass', 'interval'], order
