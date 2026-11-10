"""`/enabled_clouds/batch` filters workspaces without blocking the event loop.

The filter may reload the RBAC policy, which reads the database and can wait
for the policy's write lock. On the event loop, that wait would stall every
other request on the worker.
"""
import asyncio
import threading
import types
from unittest import mock

from sky import models
from sky.server import server
from sky.users import permission
from sky.workspaces import constants as workspace_constants


def _request(user_id):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        auth_user=models.User(id=user_id, name=user_id), request_id='req-1'))


def test_workspace_filter_runs_off_the_event_loop(monkeypatch):
    calls = []

    def accessible(user_id, workspace_names, action):
        calls.append(
            (threading.current_thread(), user_id, workspace_names, action))
        return {'a'}

    monkeypatch.setattr(permission.permission_service,
                        'get_accessible_workspace_names', accessible)
    schedule = mock.AsyncMock()
    monkeypatch.setattr(server.executor, 'schedule_request_async', schedule)

    async def call():
        await server.enabled_clouds_batch(_request('alice'), workspaces='a, b')
        return threading.current_thread()

    loop_thread = asyncio.run(call())

    assert len(calls) == 1
    thread, user_id, workspace_names, action = calls[0]
    assert thread is not loop_thread
    assert (user_id, workspace_names) == ('alice', {'a', 'b'})
    assert action == workspace_constants.WORKSPACE_ACTION_READ
    assert schedule.await_args.kwargs['request_body'].workspaces == ['a']


def test_without_auth_nothing_is_filtered(monkeypatch):
    accessible = mock.Mock()
    monkeypatch.setattr(permission.permission_service,
                        'get_accessible_workspace_names', accessible)
    schedule = mock.AsyncMock()
    monkeypatch.setattr(server.executor, 'schedule_request_async', schedule)
    request = _request('alice')
    request.state.auth_user = None

    asyncio.run(server.enabled_clouds_batch(request, workspaces='a,b'))

    accessible.assert_not_called()
    assert schedule.await_args.kwargs['request_body'].workspaces == ['a', 'b']
