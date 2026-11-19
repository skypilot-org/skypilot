"""Unit tests for the SDK fetching the cluster, storage and volume lists in
pages."""
import json
from typing import Any, Callable, Dict, List, Optional
from unittest import mock

import pytest

from sky.client import sdk
from sky.server import constants as server_constants
from sky.server import rest
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.utils import common
from sky.volumes.client import sdk as volumes_sdk

_CURSOR_VERSION = server_constants.MIN_LIST_CURSOR_API_VERSION


def _entrypoint():
    """Module-level entrypoint so Request.encode() can pickle it."""


def _payload(request_id: str, value: Any) -> Dict[str, Any]:
    request = requests_lib.Request(request_id=request_id,
                                   name='sky.list',
                                   entrypoint=_entrypoint,
                                   request_body=payloads.RequestBody(),
                                   status=requests_lib.RequestStatus.SUCCEEDED,
                                   created_at=0.0,
                                   user_id='user-1')
    request.return_value = value
    return request.encode().model_dump()


def _response(headers, json_body=None):
    response = mock.Mock()
    response.status_code = 200
    response.headers = headers
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class _Server:
    """Answers list calls from `rows`, sorted by `key`, after `after(row)`.

    Calls with a `limit` up to 2 are answered in the response; others are
    queued.
    """

    def __init__(self, rows: List[Dict[str, Any]], key: Callable[[Any], Any],
                 after: Callable[[Dict[str, Any], Dict[str, Any]], bool]):
        self.rows = sorted(rows, key=key)
        self.after = after
        self.calls: List[Dict[str, Any]] = []
        # Rows dropped from a page after its limit, as a server drops
        # records that fail validation.
        self.dropped: List[str] = []
        # Whether the server drops the cursor and answers the first page.
        self.ignores_cursor = False

    def __call__(self, method, path, **kwargs):
        args = kwargs.get('json') or kwargs.get('params') or {}
        self.calls.append({'method': method, 'path': path, **kwargs})
        request_id = f'req-{len(self.calls)}'
        limit = args.get('limit')
        if limit is None or int(limit) > 2:
            return _response({'X-Skypilot-Request-ID': request_id})
        rows = [
            row for row in self.rows
            if self.ignores_cursor or self.after(row, args)
        ]
        page = [
            row for row in rows[:int(limit)] if row['name'] not in self.dropped
        ]
        return _response({server_constants.SYNC_CALL_HEADER: '1'},
                         _payload(request_id, page))


def _rows(request_id) -> Optional[List[str]]:
    if request_id.sync_response is None:
        return None
    return [
        row['name']
        for row in json.loads(request_id.sync_response[1]['return_value'])
    ]


def _unwrapped(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _call(fn, server, api_version=_CURSOR_VERSION, **kwargs):
    with mock.patch('sky.client.common.versions.get_remote_api_version',
                    return_value=api_version), \
         mock.patch('sky.server.common.make_authenticated_request',
                    side_effect=server):
        return _unwrapped(fn)(**kwargs)


@pytest.fixture
def sync_server(monkeypatch):
    monkeypatch.setattr(rest, '_sync_call_max_limit', 2)


def _cluster_server():
    rows = [
        {
            'name': 'b-old',
            'launched_at': 100
        },
        {
            'name': 'a-new',
            'launched_at': 300
        },
        {
            'name': 'c-new',
            'launched_at': 300
        },
        {
            'name': 'e-unknown'
        },
        {
            'name': 'd-mid',
            'launched_at': 200
        },
    ]

    def key(row):
        return (-row.get('launched_at', 0), row['name'])

    def after(row, body):
        if body.get('after_name') is None:
            return True
        return key(row) > (-body['after_launched_at'], body['after_name'])

    return _Server(rows, key, after)


def _by_name_server():
    rows = [{'name': name} for name in ('n-c', 'n-a', 'n-b')]

    def after(row, params):
        return params.get('after_name') is None or (row['name'] >
                                                    params['after_name'])

    return _Server(rows, lambda row: row['name'], after)


@pytest.mark.usefixtures('sync_server')
class TestStatus:

    def test_pages_follow_the_cursor(self):
        server = _cluster_server()
        request_id = _call(sdk.status, server)
        cursors = [(c['json']['limit'], c['json']['after_launched_at'],
                    c['json']['after_name']) for c in server.calls]
        # A cluster without a launch time continues from 0.
        assert cursors == [(2, None, None), (2, 300, 'c-new'),
                           (2, 100, 'b-old'), (2, 0, 'e-unknown')]
        assert all(c['sync_call'] for c in server.calls)
        assert _rows(request_id) == [
            'a-new', 'c-new', 'd-mid', 'b-old', 'e-unknown'
        ]

    def test_a_short_page_does_not_end_the_list(self):
        server = _cluster_server()
        server.dropped = ['c-new']
        request_id = _call(sdk.status, server)
        assert _rows(request_id) == ['a-new', 'd-mid', 'b-old', 'e-unknown']

    @pytest.mark.parametrize('refresh', [
        common.StatusRefreshMode.AUTO,
        common.StatusRefreshMode.FORCE,
    ])
    def test_one_call_with_refresh(self, refresh):
        server = _cluster_server()
        request_id = _call(sdk.status, server, refresh=refresh)
        assert [c['json']['limit'] for c in server.calls] == [None]
        assert request_id == 'req-1'

    def test_one_call_on_a_server_without_the_cursor(self):
        server = _cluster_server()
        request_id = _call(sdk.status, server, api_version=_CURSOR_VERSION - 1)
        assert [c['json']['limit'] for c in server.calls] == [None]
        assert request_id == 'req-1'


@pytest.mark.usefixtures('sync_server')
class TestStorageLs:

    def test_pages_by_name(self):
        server = _by_name_server()
        request_id = _call(sdk.storage_ls, server)
        assert [c['params'] for c in server.calls] == [{
            'limit': 2
        }, {
            'limit': 2,
            'after_name': 'n-b'
        }, {
            'limit': 2,
            'after_name': 'n-c'
        }]
        assert _rows(request_id) == ['n-a', 'n-b', 'n-c']

    def test_an_ignored_cursor_falls_back_to_one_call(self):
        server = _by_name_server()
        server.ignores_cursor = True
        request_id = _call(sdk.storage_ls, server)
        assert [c.get('params') for c in server.calls] == [{
            'limit': 2
        }, {
            'limit': 2,
            'after_name': 'n-b'
        }, None]
        assert request_id == 'req-3'

    def test_queued_page_falls_back_to_one_call(self, monkeypatch):
        monkeypatch.setattr(rest, '_sync_call_max_limit', 3)
        server = _by_name_server()
        request_id = _call(sdk.storage_ls, server)
        assert [c.get('params') for c in server.calls] == [{'limit': 3}, None]
        assert request_id == 'req-2'
        assert 'sync_call' not in server.calls[1]


@pytest.mark.usefixtures('sync_server')
class TestVolumesLs:

    def test_pages_by_name_and_keeps_the_names(self):
        server = _by_name_server()
        request_id = _call(volumes_sdk.ls, server, names=['n-a', 'n-b', 'n-c'])
        assert [c['params'] for c in server.calls] == [{
            'names': ['n-a', 'n-b', 'n-c'],
            'limit': '2'
        }, {
            'names': ['n-a', 'n-b', 'n-c'],
            'limit': '2',
            'after_name': 'n-b'
        }, {
            'names': ['n-a', 'n-b', 'n-c'],
            'limit': '2',
            'after_name': 'n-c'
        }]
        assert _rows(request_id) == ['n-a', 'n-b', 'n-c']

    def test_one_call_with_refresh(self):
        server = _by_name_server()
        request_id = _call(volumes_sdk.ls, server, refresh=True)
        assert [c['params'] for c in server.calls] == [{'refresh': 'true'}]
        assert request_id == 'req-1'


def test_one_call_without_sync_calls(monkeypatch):
    monkeypatch.setattr(rest, '_sync_call_max_limit', None)
    server = _by_name_server()
    request_id = _call(sdk.storage_ls, server)
    assert len(server.calls) == 1 and request_id == 'req-1'
