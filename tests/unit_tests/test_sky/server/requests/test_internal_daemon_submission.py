"""Unit tests for the daemon submission helpers.

`create_or_refresh_internal_daemon_async` mirrors `create_if_not_exists_async`
for daemon requests: exactly one concurrent caller wins the insert race and
gets True (and is expected to enqueue), every other caller gets False after
refreshing env_vars / name / schedule_type on the existing row.

`delete_orphan_internal_daemons_async` removes daemon-shaped rows whose ids
are no longer in INTERNAL_REQUEST_DAEMONS.

The PG-backed implementation in the HA plugin is tested separately.
"""
import unittest.mock as mock

import pytest

from sky.server import daemons
from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType


def _dummy_event():
    return None


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated database per test."""
    temp_db_path = tmp_path / 'requests.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()

    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)), \
         mock.patch('sky.server.constants.REQUEST_LOG_PATH_PREFIX',
                    str(temp_log_path)):
        requests._DB = None
        yield tmp_path
        requests._DB = None


def _make_daemon(daemon_id: str) -> daemons.InternalRequestDaemon:
    return daemons.InternalRequestDaemon(
        id=daemon_id,
        name=daemons.request_names.RequestName.REQUEST_DAEMON_STATUS_REFRESH,
        event_fn=_dummy_event,
    )


@pytest.mark.asyncio
async def test_create_or_refresh_inserts_new_row(isolated_database,
                                                 monkeypatch):
    """First caller against an empty DB: row inserted, returns True."""
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'fresh')
    daemon = _make_daemon('alpha-daemon')
    req = requests.build_internal_daemon_request(daemon)
    assert (await requests.create_or_refresh_internal_daemon_async(req)) is True

    row = await requests.get_request_async('alpha-daemon')
    assert row is not None
    assert row.status == RequestStatus.PENDING
    assert row.pid is None
    assert row.request_body.env_vars.get('SKYPILOT_RECON_TAG') == 'fresh'


@pytest.mark.asyncio
async def test_create_or_refresh_existing_row_returns_false_and_refreshes_env(
        isolated_database, monkeypatch):
    """Second caller against an existing row: returns False, env refreshed."""
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'old')
    daemon = _make_daemon('alpha-daemon')
    req_first = requests.build_internal_daemon_request(daemon)
    assert (await
            requests.create_or_refresh_internal_daemon_async(req_first)) is True

    # Pretend a deploy happened: env flipped, second caller builds a fresh
    # Request with the new env.
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'new')
    req_second = requests.build_internal_daemon_request(daemon)
    assert (await requests.create_or_refresh_internal_daemon_async(req_second)
           ) is False

    row = await requests.get_request_async('alpha-daemon')
    assert row.request_body.env_vars.get('SKYPILOT_RECON_TAG') == 'new'


@pytest.mark.asyncio
async def test_delete_orphan_removes_rows_not_in_current_set(isolated_database):
    """A '-daemon' row whose id is not in the current set should be deleted;
    a row whose id IS in the current set should survive."""
    # Seed a "current" daemon row.
    keep_req = requests.build_internal_daemon_request(
        _make_daemon('alpha-daemon'))
    assert await requests.create_if_not_exists_async(keep_req)
    # Seed a "legacy" daemon row directly.
    legacy = requests.Request(
        request_id='removed-daemon',
        name='sky.removed',
        entrypoint=_dummy_event,
        request_body=payloads.RequestBody(),
        status=RequestStatus.PENDING,
        created_at=0.0,
        schedule_type=ScheduleType.SHORT,
        user_id='__system__',
    )
    assert await requests.create_if_not_exists_async(legacy)

    await requests.delete_orphan_internal_daemons_async(
        [_make_daemon('alpha-daemon')])

    assert await requests.get_request_async('removed-daemon') is None
    assert await requests.get_request_async('alpha-daemon') is not None


@pytest.mark.asyncio
async def test_delete_orphan_ignores_non_daemon_request_ids(isolated_database):
    """Rows whose id does not end in '-daemon' must not be touched even if
    they are not in the current set."""
    other = requests.Request(
        request_id='some-client-request-uuid',
        name='sky.launch',
        entrypoint=_dummy_event,
        request_body=payloads.RequestBody(),
        status=RequestStatus.PENDING,
        created_at=0.0,
        schedule_type=ScheduleType.SHORT,
        user_id='__system__',
    )
    assert await requests.create_if_not_exists_async(other)

    await requests.delete_orphan_internal_daemons_async([])

    assert await requests.get_request_async('some-client-request-uuid'
                                           ) is not None
