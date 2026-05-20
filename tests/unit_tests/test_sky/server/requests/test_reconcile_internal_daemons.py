"""Unit tests for the default RequestBackend.reconcile_internal_daemons_async.

The OSS SQLite backend inherits the base-class default, which simply calls
`create_if_not_exists_async` for each non-skip daemon and returns the list
for the caller to enqueue. The OSS SQLite DB is wiped on every
`sky api start` via `reset_db_and_logs`, so there are no persistent stale
rows to reconcile in this backend. The PG-backed implementation in the HA
plugin overrides this method to do the actual stale-row reconciliation
(tested separately on the plugin side).
"""
import unittest.mock as mock

import pytest

from sky.server import daemons
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus


def _dummy_event():
    return None


def _skip_true():
    return True


def _skip_false():
    return False


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
async def test_reconcile_inserts_each_non_skip_daemon(isolated_database):
    """Empty DB: each non-skip daemon should get a fresh PENDING row."""
    daemons_list = [
        _make_daemon('alpha-daemon'),
        _make_daemon('beta-daemon'),
    ]
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert {r.request_id for r in enqueued} == {'alpha-daemon', 'beta-daemon'}
    assert all(r.status == RequestStatus.PENDING for r in enqueued)

    for daemon_id in ('alpha-daemon', 'beta-daemon'):
        row = await requests.get_request_async(daemon_id)
        assert row is not None
        assert row.status == RequestStatus.PENDING
        assert row.pid is None


@pytest.mark.asyncio
async def test_reconcile_skips_should_skip_daemons(isolated_database):
    """Daemons whose should_skip() returns True are not inserted and not
    returned for enqueue."""
    daemons_list = [
        _make_daemon('alpha-daemon'),
        _make_daemon('beta-daemon', should_skip=True),
    ]
    enqueued = await requests.reconcile_internal_daemons_async(daemons_list)
    assert {r.request_id for r in enqueued} == {'alpha-daemon'}
    assert await requests.get_request_async('beta-daemon') is None


@pytest.mark.asyncio
async def test_reconcile_idempotent_on_repeat(isolated_database):
    """Calling reconcile twice on the same daemons should still return the
    full enqueue list both times (the underlying create_if_not_exists is a
    no-op on the second call, but reconcile still flags every non-skip
    daemon as needing an in-memory enqueue)."""
    daemons_list = [_make_daemon('alpha-daemon')]
    enqueued_first = await requests.reconcile_internal_daemons_async(
        daemons_list)
    enqueued_second = await requests.reconcile_internal_daemons_async(
        daemons_list)
    assert len(enqueued_first) == 1
    assert len(enqueued_second) == 1
    assert (enqueued_first[0].request_id == enqueued_second[0].request_id ==
            'alpha-daemon')
