"""Unit tests for SqliteRequestBackend.reconcile_internal_daemons_async."""
import os
import unittest.mock as mock

import pytest

from sky.server import daemons
from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType


def _dummy_event():
    return None


def _dummy_other_event():
    return None


def _skip_true():
    return True


def _skip_false():
    return False


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated database + reconcile lock path per test."""
    temp_db_path = tmp_path / 'requests.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()
    lock_path = str(tmp_path / 'reconcile_internal_daemons.lock')

    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)), \
         mock.patch('sky.server.constants.REQUEST_LOG_PATH_PREFIX',
                    str(temp_log_path)), \
         mock.patch('sky.server.requests.requests._reconcile_lock_path',
                    return_value=lock_path):
        requests._DB = None
        yield tmp_path
        requests._DB = None


def _make_daemon(daemon_id: str,
                 event_fn=_dummy_event,
                 should_skip=False) -> daemons.InternalRequestDaemon:
    return daemons.InternalRequestDaemon(
        id=daemon_id,
        # Reuse the managed-job name enum: tests don't care which name, just
        # that the row column is consistent.
        name=daemons.request_names.RequestName.REQUEST_DAEMON_STATUS_REFRESH,
        event_fn=event_fn,
        should_skip=_skip_true if should_skip else _skip_false,
    )


@pytest.mark.asyncio
async def test_reconcile_inserts_missing_rows(isolated_database, monkeypatch):
    """Empty DB: each daemon should get a fresh PENDING row."""
    monkeypatch.setenv('SKYPILOT_RECONCILE_TEST_TAG', 'fresh')
    daemons_list = [_make_daemon('alpha-daemon')]

    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert len(enqueued) == 1
    assert enqueued[0].request_id == 'alpha-daemon'
    assert enqueued[0].status == RequestStatus.PENDING

    row = await requests.get_request_async('alpha-daemon')
    assert row is not None
    assert row.status == RequestStatus.PENDING
    assert row.pid is None
    assert row.request_body.env_vars.get(
        'SKYPILOT_RECONCILE_TEST_TAG') == 'fresh'


@pytest.mark.asyncio
async def test_reconcile_refreshes_env_vars_on_existing_row(
        isolated_database, monkeypatch):
    """A pre-existing row with old env_vars should be refreshed."""
    monkeypatch.setenv('SKYPILOT_RECONCILE_TEST_TAG', 'old')
    daemons_list = [_make_daemon('alpha-daemon')]

    # Initial reconcile creates the row with tag='old'.
    await requests.reconcile_internal_daemons_async(daemons_list)

    # Simulate a deployment generation flip: change env, reconcile again.
    monkeypatch.setenv('SKYPILOT_RECONCILE_TEST_TAG', 'new')
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert len(enqueued) == 1

    row = await requests.get_request_async('alpha-daemon')
    assert row.request_body.env_vars.get('SKYPILOT_RECONCILE_TEST_TAG') == 'new'


@pytest.mark.asyncio
async def test_reconcile_deletes_orphan_daemon_row(isolated_database):
    """A '*-daemon' row whose id is not in the current list should be
    deleted."""
    daemons_list = [_make_daemon('alpha-daemon')]
    await requests.reconcile_internal_daemons_async(daemons_list)

    # Seed a "legacy" daemon row directly.
    legacy = requests.Request(
        request_id='removed-daemon',
        name='sky.removed',
        entrypoint=_dummy_other_event,
        request_body=payloads.RequestBody(),
        status=RequestStatus.PENDING,
        created_at=0.0,
        schedule_type=ScheduleType.SHORT,
        user_id='__system__',
    )
    assert await requests.create_if_not_exists_async(legacy)

    # Reconcile with the current list (no 'removed-daemon').
    await requests.reconcile_internal_daemons_async(daemons_list)
    assert await requests.get_request_async('removed-daemon') is None
    assert await requests.get_request_async('alpha-daemon') is not None


@pytest.mark.asyncio
async def test_reconcile_terminal_row_reset_to_pending(isolated_database):
    """A daemon row in a terminal state should be reset to PENDING."""
    daemons_list = [_make_daemon('alpha-daemon')]
    await requests.reconcile_internal_daemons_async(daemons_list)

    # Force the row to FAILED by going through update_request.
    with requests.update_request('alpha-daemon') as r:
        assert r is not None
        r.status = RequestStatus.FAILED
        r.pid = 99999  # bogus pid
        r.finished_at = 1.0

    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert len(enqueued) == 1
    row = await requests.get_request_async('alpha-daemon')
    assert row.status == RequestStatus.PENDING
    assert row.pid is None


@pytest.mark.asyncio
async def test_reconcile_running_row_with_live_pid_preserved(
        isolated_database, monkeypatch):
    """RUNNING row with a live local pid stays RUNNING; env_vars refreshed;
    entrypoint NOT touched."""
    monkeypatch.setenv('SKYPILOT_RECONCILE_TEST_TAG', 'old')
    daemons_list = [_make_daemon('alpha-daemon')]
    await requests.reconcile_internal_daemons_async(daemons_list)

    # Mark as RUNNING with our own pid (definitely alive).
    with requests.update_request('alpha-daemon') as r:
        assert r is not None
        r.status = RequestStatus.RUNNING
        r.pid = os.getpid()
        # Sentinel entrypoint to verify it isn't replaced.
        r.entrypoint = _dummy_other_event

    monkeypatch.setenv('SKYPILOT_RECONCILE_TEST_TAG', 'new')
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert len(enqueued) == 0  # running-on-live-pid daemons aren't enqueued

    row = await requests.get_request_async('alpha-daemon')
    assert row.status == RequestStatus.RUNNING
    assert row.pid == os.getpid()
    # Entrypoint preserved.
    assert row.entrypoint is _dummy_other_event
    # env_vars refreshed.
    assert row.request_body.env_vars.get('SKYPILOT_RECONCILE_TEST_TAG') == 'new'


@pytest.mark.asyncio
async def test_reconcile_should_skip_deletes_dormant_row(isolated_database):
    """A daemon whose should_skip()==True with no live local pid should have
    its row deleted."""
    daemons_list = [_make_daemon('alpha-daemon')]
    await requests.reconcile_internal_daemons_async(daemons_list)
    assert await requests.get_request_async('alpha-daemon') is not None

    # Flip to should_skip=True.
    daemons_list_skip = [_make_daemon('alpha-daemon', should_skip=True)]
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list_skip
                                                              )
    assert enqueued == []
    assert await requests.get_request_async('alpha-daemon') is None


@pytest.mark.asyncio
async def test_reconcile_should_skip_preserves_running_on_live_pid(
        isolated_database):
    """If a daemon's should_skip() is True but a row is currently RUNNING on
    a live local pid, the row should be preserved (don't yank a running
    daemon mid-config-flip)."""
    daemons_list = [_make_daemon('alpha-daemon')]
    await requests.reconcile_internal_daemons_async(daemons_list)

    with requests.update_request('alpha-daemon') as r:
        assert r is not None
        r.status = RequestStatus.RUNNING
        r.pid = os.getpid()

    daemons_list_skip = [_make_daemon('alpha-daemon', should_skip=True)]
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list_skip
                                                              )
    assert enqueued == []
    row = await requests.get_request_async('alpha-daemon')
    assert row is not None
    assert row.status == RequestStatus.RUNNING
    assert row.pid == os.getpid()


@pytest.mark.asyncio
async def test_reconcile_returns_caller_enqueue_set(isolated_database):
    """The returned list should only contain daemons that need a fresh
    enqueue — not should_skip daemons and not running-on-live-pid daemons."""
    daemons_list = [
        _make_daemon('alpha-daemon'),
        _make_daemon('beta-daemon'),
        _make_daemon('gamma-daemon'),
    ]
    await requests.reconcile_internal_daemons_async(daemons_list)

    # Mark gamma RUNNING-on-live-pid.
    with requests.update_request('gamma-daemon') as r:
        assert r is not None
        r.status = RequestStatus.RUNNING
        r.pid = os.getpid()

    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    enqueued_ids = {r.request_id for r in enqueued}
    # alpha and beta were PENDING -> they get re-upserted to PENDING and need
    # enqueue. gamma is preserved RUNNING -> not in the list.
    assert enqueued_ids == {'alpha-daemon', 'beta-daemon'}
