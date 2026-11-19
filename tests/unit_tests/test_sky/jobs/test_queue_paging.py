"""Unit tests for the SDK fetching the managed jobs queue in pages."""
from typing import Dict, List
from unittest import mock

import pytest

from sky.client import sdk
from sky.jobs.client import sdk as jobs_sdk
from sky.server import constants as server_constants
from sky.server import rest
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib

_CURSOR_VERSION = server_constants.MIN_JOBS_QUEUE_CURSOR_API_VERSION


def _entrypoint():
    """Module-level entrypoint so Request.encode() can pickle it."""


def _request(request_id, value=None, error=None):
    request = requests_lib.Request(request_id=request_id,
                                   name=server_constants.REQUEST_NAME_PREFIX +
                                   'jobs.queue_v2',
                                   entrypoint=_entrypoint,
                                   request_body=payloads.RequestBody(),
                                   status=requests_lib.RequestStatus.SUCCEEDED,
                                   created_at=0.0,
                                   user_id='user-1')
    if error is not None:
        request.status = requests_lib.RequestStatus.FAILED
        request.set_error(error)
    else:
        request.return_value = value
    return request.encode().model_dump()


def _response(status_code, headers, json_body=None):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def _sync_response(status_code, json_body):
    return _response(status_code, {server_constants.SYNC_CALL_HEADER: '1'},
                     json_body)


class _Server:
    """Answers queue calls from top-level jobs and the jobs under them.

    `trees` maps each root id to its members' ids. Calls with a `limit` up to
    `max_limit` are answered in the response; others are queued.
    """

    def __init__(self, trees: Dict[int, List[int]], max_limit: int = 2):
        self.trees = trees
        self.max_limit = max_limit
        self.bodies: List[dict] = []
        # Request ids of the calls answered with this error, if any.
        self.errors: Dict[int, Exception] = {}

    def __call__(self, method, path, json=None, timeout=None, sync_call=False):
        del timeout  # Unused.
        assert (method, path, sync_call) == ('POST', '/jobs/queue/v2', True)
        self.bodies.append(json)
        call = len(self.bodies)
        request_id = f'req-{call}'
        limit = json['limit']
        if limit is None or limit > self.max_limit:
            return _response(200, {'X-Skypilot-Request-ID': request_id})
        if call in self.errors:
            body = _request(request_id, error=self.errors[call])
            return _sync_response(500, {'detail': body})
        roots = sorted(self.trees, reverse=True)
        if json['after_job_id'] is not None:
            roots = [root for root in roots if root < json['after_job_id']]
        jobs = []
        for root in roots[:limit]:
            jobs.append(_job(root, None))
            jobs.extend(_job(member, root) for member in self.trees[root])
        value = {
            'jobs': jobs,
            'total': len(self.trees),
            'total_no_filter': len(self.trees),
            'status_counts': {
                'PENDING': len(self.trees)
            },
            'infra_options': [],
        }
        return _sync_response(200, _request(request_id, value=value))


def _job(job_id, root_job_id):
    return {
        'job_id': job_id,
        'task_id': 0,
        'status': 'PENDING',
        'root_job_id': root_job_id,
    }


_TREES = {9: [10], 7: [], 5: [11], 3: [], 1: []}


@pytest.fixture
def sync_server(monkeypatch):
    monkeypatch.setattr(rest, '_sync_call_max_limit', 2)


def _queue_v2(server, api_version=_CURSOR_VERSION, **kwargs):
    raw_queue_v2 = jobs_sdk.queue_v2
    while hasattr(raw_queue_v2, '__wrapped__'):
        raw_queue_v2 = raw_queue_v2.__wrapped__
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=api_version), \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           side_effect=server):
        return raw_queue_v2(refresh=False, **kwargs)


def _job_ids(request_id):
    jobs, total, _, _ = sdk.get(request_id)
    return sorted(job.job_id for job in jobs), total


@pytest.mark.usefixtures('sync_server')
class TestPaging:

    def test_pages_until_an_empty_page(self):
        server = _Server(_TREES)
        request_id = _queue_v2(server)
        assert [(b['limit'], b['after_job_id']) for b in server.bodies
               ] == [(2, None), (2, 7), (2, 3), (2, 1)]
        assert 'root_job_id' in server.bodies[0]['fields']
        assert _job_ids(request_id) == ([1, 3, 5, 7, 9, 10, 11], 5)

    def test_stops_after_an_empty_page(self):
        server = _Server({4: [], 3: [], 2: [], 1: []})
        request_id = _queue_v2(server)
        assert [b['after_job_id'] for b in server.bodies] == [None, 3, 1]
        assert _job_ids(request_id) == ([1, 2, 3, 4], 4)

    def test_stops_at_the_limit(self):
        server = _Server(_TREES)
        request_id = _queue_v2(server, limit=3)
        assert [(b['limit'], b['after_job_id']) for b in server.bodies
               ] == [(2, None), (1, 7)]
        assert _job_ids(request_id) == ([5, 7, 9, 10, 11], 5)

    @pytest.mark.parametrize('fields', [None, []])
    def test_all_fields_carry_the_root(self, fields):
        server = _Server(_TREES)
        _queue_v2(server, fields=fields)
        assert server.bodies[0]['fields'] == fields

    @pytest.mark.parametrize('kwargs', [{
        'limit': 2
    }, {
        'sort_by': 'name'
    }, {
        'sort_by': 'job_id',
        'sort_order': 'asc'
    }, {
        'job_ids': [1],
        'include_tree': True
    }])
    def test_one_call_when_paging_does_not_apply(self, kwargs):
        server = _Server(_TREES)
        _queue_v2(server, **kwargs)
        assert len(server.bodies) == 1
        assert server.bodies[0]['after_job_id'] is None

    def test_one_call_on_a_server_without_the_cursor(self):
        server = _Server(_TREES)
        request_id = _queue_v2(server, api_version=_CURSOR_VERSION - 1)
        assert [b['limit'] for b in server.bodies] == [None]
        assert request_id == 'req-1'

    def test_queued_page_falls_back_to_one_call(self):
        server = _Server(_TREES, max_limit=1)
        request_id = _queue_v2(server)
        assert [b['limit'] for b in server.bodies] == [2, None]
        assert request_id == 'req-2'
        assert request_id.sync_response is None

    def test_refused_cursor_falls_back_to_one_call(self):
        server = _Server(_TREES)
        server.errors[2] = jobs_sdk.exceptions.NotSupportedError('too old')
        request_id = _queue_v2(server)
        assert [b['limit'] for b in server.bodies] == [2, 2, None]
        assert request_id == 'req-3'

    def test_failed_page_raises_from_get(self):
        server = _Server(_TREES)
        server.errors[2] = ValueError('boom')
        request_id = _queue_v2(server)
        assert len(server.bodies) == 2
        with pytest.raises(ValueError, match='boom'):
            sdk.get(request_id)


def test_one_call_without_sync_calls(monkeypatch):
    monkeypatch.setattr(rest, '_sync_call_max_limit', None)
    server = _Server(_TREES)
    request_id = _queue_v2(server)
    assert [b['limit'] for b in server.bodies] == [None]
    assert request_id == 'req-1'
