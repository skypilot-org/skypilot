"""Tests for requests-GC batch pacing and daemon startup jitter."""
import asyncio
from unittest import mock

import pytest

from sky.server.requests import requests as requests_lib
from sky.utils import asyncio_utils


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
    # Two identical values across 50 draws would mean it is not random.
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
async def test_gc_pauses_between_batches_but_not_after_last():
    """A full batch is followed by a pause; a short (final) batch is not."""
    batches = [
        [mock.Mock(request_id=f'r{i}') for i in range(2)],  # full batch
        [mock.Mock(request_id='last')],  # short -> stop
    ]
    for batch in batches:
        for req in batch:
            req.log_path = mock.Mock()
            req.log_path.absolute.return_value = '/nonexistent/x.log'

    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    async def fake_get(**_):
        return batches.pop(0) if batches else []

    with mock.patch.object(requests_lib, 'get_request_tasks_async', fake_get), \
         mock.patch.object(requests_lib, '_delete_requests',
                           mock.AsyncMock()), \
         mock.patch.object(requests_lib, '_cleanup_legacy_directory_if_empty',
                           mock.AsyncMock()), \
         mock.patch('anyio.Path.unlink', mock.AsyncMock()), \
         mock.patch.object(asyncio, 'sleep', fake_sleep):
        await requests_lib.clean_finished_requests_with_retention(
            retention_seconds=0, batch_size=2, batch_pause_seconds=7)

    # Exactly one pause: after the full batch, not after the short final one.
    assert sleeps == [7], sleeps


@pytest.mark.asyncio
async def test_gc_pause_zero_disables_sleeping():
    batches = [[mock.Mock(request_id='r0')]]
    for req in batches[0]:
        req.log_path = mock.Mock()
        req.log_path.absolute.return_value = '/nonexistent/x.log'

    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    async def fake_get(**_):
        return batches.pop(0) if batches else []

    with mock.patch.object(requests_lib, 'get_request_tasks_async', fake_get), \
         mock.patch.object(requests_lib, '_delete_requests',
                           mock.AsyncMock()), \
         mock.patch.object(requests_lib, '_cleanup_legacy_directory_if_empty',
                           mock.AsyncMock()), \
         mock.patch('anyio.Path.unlink', mock.AsyncMock()), \
         mock.patch.object(asyncio, 'sleep', fake_sleep):
        await requests_lib.clean_finished_requests_with_retention(
            retention_seconds=0, batch_size=1000, batch_pause_seconds=0)

    assert not sleeps


@pytest.mark.asyncio
async def test_gc_skips_legacy_unlink_when_dir_absent(tmp_path):
    """The legacy per-request unlink is skipped once the directory is gone.

    On a migrated server the pre-v0.15 directory does not exist, so paying a
    syscall per row for it is waste -- and on deployments that put ~/sky_logs
    on a shared filesystem, it is a network round trip per row.
    """
    reqs = [mock.Mock(request_id=f'r{i}') for i in range(3)]
    for req in reqs:
        req.log_path = mock.Mock()
        req.log_path.absolute.return_value = str(tmp_path / 'x.log')
    batches = [reqs]

    async def fake_get(**_):
        return batches.pop(0) if batches else []

    unlinked = []

    async def fake_unlink(self, missing_ok=False):
        unlinked.append(str(self))

    absent = tmp_path / 'definitely-absent'
    with mock.patch.object(requests_lib, 'get_request_tasks_async', fake_get), \
         mock.patch.object(requests_lib, '_delete_requests',
                           mock.AsyncMock()), \
         mock.patch.object(requests_lib, '_cleanup_legacy_directory_if_empty',
                           mock.AsyncMock()), \
         mock.patch.object(requests_lib, 'LEGACY_REQUEST_LOG_PATH_PREFIX',
                           str(absent)), \
         mock.patch('anyio.Path.unlink', fake_unlink):
        await requests_lib.clean_finished_requests_with_retention(
            retention_seconds=0, batch_size=1000, batch_pause_seconds=0)

    assert not any(str(absent) in p for p in unlinked), unlinked
    # Two unlinks per row (current log + debug log) rather than three.
    assert len(unlinked) == 2 * len(reqs), unlinked
