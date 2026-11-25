"""Blocking calls in async handlers run off the event loop.

Each patched blocking call records the thread it ran on. `asyncio.run` runs
the loop on the test's own thread, so a call made inline records that thread.
"""
import asyncio
import threading
from typing import Any, List
from unittest import mock

import fastapi
import pytest

import sky
from sky import models
from sky.jobs.server import server as jobs_server
from sky.server import server
from sky.server import version_check
from sky.server.auth import sessions as auth_sessions
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.server.requests import role_filter
from sky.skylet import constants
from sky.users import permission

_ENV = {constants.USER_ID_ENV_VAR: 'x', constants.USER_ENV_VAR: 'x'}


class _Recorder:
    """A stand-in for a blocking call that records the thread it ran on."""

    def __init__(self, result: Any = None):
        self.result = result
        self.threads: List[int] = []

    def __call__(self, *args, **kwargs):
        del args, kwargs
        self.threads.append(threading.get_ident())
        return self.result

    def assert_ran_off_loop_once(self):
        assert len(self.threads) == 1, self.threads
        assert self.threads[0] != threading.get_ident()


def _request(user_id='alice'):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.request_id = 'request-id'
    request.state.auth_user = models.User(id=user_id, name=user_id)
    return request


@pytest.fixture(name='schedule')
def _schedule(monkeypatch):
    schedule = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', schedule)
    return schedule


def test_enabled_clouds_batch_filters_off_loop(schedule, monkeypatch):
    accessible = _Recorder({'ws-a'})
    monkeypatch.setattr(permission.permission_service,
                        'get_accessible_workspace_names', accessible)
    asyncio.run(server.enabled_clouds_batch(_request(), workspaces='ws-a,ws-b'))
    accessible.assert_ran_off_loop_once()
    body = schedule.await_args.kwargs['request_body']
    assert body.workspaces == ['ws-a']


def test_api_status_encodes_off_loop(monkeypatch):
    tasks = []
    for user_id in ['alice', 'bob', 'alice']:
        task = mock.MagicMock(user_id=user_id)
        task.readable_encode = _Recorder(f'encoded-{len(tasks)}')
        tasks.append(task)
    monkeypatch.setattr(requests_lib, 'get_requests_async_with_prefix',
                        mock.AsyncMock(return_value=tasks))
    monkeypatch.setattr(role_filter, 'request_owner_scope', lambda _: 'alice')
    result = asyncio.run(
        server.api_status(_request(),
                          request_ids=['req'],
                          all_status=False,
                          limit=None,
                          fields=None,
                          cluster_name=None))
    # bob's task is filtered out before encoding.
    assert result == ['encoded-0', 'encoded-2']
    tasks[0].readable_encode.assert_ran_off_loop_once()
    tasks[2].readable_encode.assert_ran_off_loop_once()
    assert not tasks[1].readable_encode.threads


def test_poll_auth_token_off_loop(monkeypatch):
    poll = _Recorder('token')
    monkeypatch.setattr(auth_sessions.auth_session_store, 'poll_session', poll)
    response = asyncio.run(server.poll_auth_token(code_verifier='verifier'))
    poll.assert_ran_off_loop_once()
    assert response.body == b'{"token":"token"}'


def test_authorize_auth_session_off_loop(monkeypatch):
    create = _Recorder()
    monkeypatch.setattr(auth_sessions.auth_session_store, 'create_session',
                        create)
    monkeypatch.setattr(server, '_generate_auth_token', lambda _: 'token')
    request = _request()
    request.json = mock.AsyncMock(return_value={'code_challenge': 'c' * 43})
    response = asyncio.run(server.authorize_auth_session(request))
    create.assert_ran_off_loop_once()
    assert response.status_code == 200


class TestVersionCheck:
    """`/api/health` reads the version cache only; the periodic task fetches."""

    @pytest.fixture(autouse=True)
    def _no_pypi(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sky, '__version__', '1.0.0')
        monkeypatch.setattr(version_check, 'VERSION_CACHE_FILE',
                            tmp_path / 'version_cache.json')
        monkeypatch.setattr(version_check, '_version_cache', None)
        self.get = mock.Mock(side_effect=AssertionError('fetched PyPI'))
        monkeypatch.setattr(version_check.requests, 'get', self.get)

    def test_expired_cache_is_served_without_fetching(self):
        version_check._save_cache({  # pylint: disable=protected-access
            'release_version': '2.0.0',
            'cached_at': 0,
        })
        assert version_check.get_latest_version_for_current() == '2.0.0'
        self.get.assert_not_called()

    def test_no_cache_means_no_latest_version(self):
        assert version_check.get_latest_version_for_current() is None
        self.get.assert_not_called()

    def test_periodic_task_fetches_off_loop(self, monkeypatch):

        class _Stop(Exception):
            pass

        fetch = _Recorder('2.0.0')
        monkeypatch.setattr(version_check, 'get_latest_versions', fetch)
        with mock.patch.object(version_check.asyncio,
                               'sleep',
                               side_effect=_Stop):
            with pytest.raises(_Stop):
                asyncio.run(version_check.check_versions_periodically())
        fetch.assert_ran_off_loop_once()

    def test_periodic_task_refetches_soon_after_expiry(self, monkeypatch):

        class _Stop(Exception):
            pass

        hour = 60 * 60
        boot = 1000 * hour
        now = [boot]
        monkeypatch.setattr(version_check, 'time',
                            mock.Mock(time=lambda: now[0]))
        version_check._save_cache({  # pylint: disable=protected-access
            'release_version': '2.0.0',
            'cached_at': boot - 23 * hour,
        })
        fetched_at = []

        def fetch():
            fetched_at.append(now[0])
            return '3.0.0'

        monkeypatch.setattr(version_check, '_check_pypi_versions', fetch)

        async def sleep(seconds):
            now[0] += seconds
            if now[0] - boot > 25 * hour:
                raise _Stop

        with mock.patch.object(version_check.asyncio, 'sleep', sleep):
            with pytest.raises(_Stop):
                asyncio.run(version_check.check_versions_periodically())
        # Refetched an hour after expiry, then once per TTL as before.
        assert fetched_at == [boot + hour, boot + 25 * hour]


class TestJobsConsolidationMode:
    """The consolidation-mode check runs off the loop, once per request."""

    @pytest.fixture(autouse=True)
    def _executor(self, monkeypatch, schedule, tmp_path):
        self.schedule = schedule
        self.prepare = mock.AsyncMock()
        monkeypatch.setattr(executor, 'prepare_request_async', self.prepare)
        for name in [
                'check_request_thread_executor_available',
                'execute_request_in_coroutine'
        ]:
            monkeypatch.setattr(executor, name, mock.Mock())
        monkeypatch.setattr(executor, 'schedule_prepared_request',
                            mock.AsyncMock())
        monkeypatch.setattr(jobs_server.stream_utils,
                            'stream_response_for_long_request', mock.Mock())
        monkeypatch.setattr(jobs_server.download_utils, 'owner_user_id',
                            lambda *_: 'alice')
        monkeypatch.setattr(
            jobs_server.bs, 'get_blob_storage', lambda: mock.Mock(
                download_tmp_dir=lambda _: str(tmp_path / 'logs')))

    def _use_mode(self, monkeypatch, consolidation):
        self.mode = _Recorder(consolidation)
        monkeypatch.setattr(jobs_server.managed_jobs_utils,
                            'is_consolidation_mode', self.mode)

    def _call(self, route, refresh):
        request = _request()
        if route == 'queue':
            body = payloads.JobsQueueBody(refresh=refresh, env_vars=_ENV)
            coro = jobs_server.queue(request, body)
        elif route == 'queue_v2':
            body = payloads.JobsQueueV2Body(refresh=refresh, env_vars=_ENV)
            coro = jobs_server.queue_v2(request, body)
        elif route == 'logs':
            body = payloads.JobsLogsBody(refresh=refresh, env_vars=_ENV)
            coro = jobs_server.logs(request, mock.Mock(), body)
        else:
            body = payloads.JobsDownloadLogsBody(name=None,
                                                 job_id=1,
                                                 refresh=refresh,
                                                 env_vars=_ENV)
            coro = jobs_server.download_logs(request, body)
        asyncio.run(coro)
        scheduled = self.prepare if route == 'logs' else self.schedule
        scheduled.assert_awaited_once()
        return scheduled.await_args.kwargs['schedule_type']

    def test_launch(self, monkeypatch):
        self._use_mode(monkeypatch, False)
        body = payloads.JobsLaunchBody(task='name: t', name=None, env_vars=_ENV)
        asyncio.run(jobs_server.launch(_request(), body))
        self.mode.assert_ran_off_loop_once()

    @pytest.mark.parametrize('route',
                             ['queue', 'queue_v2', 'logs', 'download_logs'])
    @pytest.mark.parametrize('refresh,consolidation,expected', [
        (False, False, requests_lib.ScheduleType.SHORT),
        (True, False, requests_lib.ScheduleType.LONG),
        (True, True, requests_lib.ScheduleType.SHORT),
    ])
    def test_refresh_routes(self, monkeypatch, route, refresh, consolidation,
                            expected):
        self._use_mode(monkeypatch, consolidation)
        assert self._call(route, refresh) == expected
        if refresh:
            self.mode.assert_ran_off_loop_once()
        else:
            # No refresh, no need to know the mode.
            assert not self.mode.threads
