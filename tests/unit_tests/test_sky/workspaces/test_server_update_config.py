"""Tests for the POST /workspaces/config route.

The route schedules the config save and then a `sky check` request gated on
the save succeeding. These tests pin the shape of the two scheduled requests.
"""
from unittest import mock

import pytest

from sky import check as sky_check
from sky import models
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import request_names
from sky.server.requests import requests as api_requests
from sky.workspaces import core
from sky.workspaces import server as workspaces_server


def _fake_request(auth_user):
    request = mock.MagicMock()
    request.state = mock.MagicMock()
    request.state.request_id = 'save-request-id'
    request.state.auth_user = auth_user
    return request


@pytest.mark.asyncio
async def test_update_config_schedules_save_then_gated_check():
    auth_user = models.User(id='user-1', name='User One')
    request = _fake_request(auth_user)
    body = payloads.UpdateConfigBody(config={'workspaces': {'default': {}}})
    body.env_vars = {'SKYPILOT_USER_ID': 'user-1', 'SKYPILOT_USER': 'User One'}

    with mock.patch('sky.workspaces.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await workspaces_server.update_config(request, body)

    assert mock_schedule.await_count == 2
    save_kwargs = mock_schedule.await_args_list[0].kwargs
    check_kwargs = mock_schedule.await_args_list[1].kwargs

    # The save is unchanged: same request id, same function, no precondition.
    assert save_kwargs['request_id'] == 'save-request-id'
    assert (save_kwargs['request_name'] ==
            request_names.RequestName.WORKSPACES_UPDATE_CONFIG)
    assert save_kwargs['func'] is core.update_config
    assert save_kwargs['request_body'] is body
    assert save_kwargs['auth_user'] is auth_user
    assert 'precondition' not in save_kwargs

    # The check is a separate request with its own id.
    assert check_kwargs['request_id'] != 'save-request-id'
    assert check_kwargs['request_name'] == request_names.RequestName.CHECK
    assert check_kwargs['func'] is sky_check.check
    assert check_kwargs['schedule_type'] == api_requests.ScheduleType.SHORT
    assert check_kwargs['auth_user'] is auth_user

    # The check runs as the same user as the save when auth_user is absent.
    check_body = check_kwargs['request_body']
    assert isinstance(check_body, payloads.CheckBody)
    assert check_body.env_vars == body.env_vars
    assert check_body.env_vars is not body.env_vars

    # The check is gated on the save request succeeding.
    precondition = check_kwargs['precondition']
    assert isinstance(precondition, preconditions.RequestSucceededPrecondition)
    assert precondition.request_id == check_kwargs['request_id']
    assert precondition.awaited_request_id == 'save-request-id'


@pytest.mark.asyncio
async def test_update_config_without_auth_user():
    request = _fake_request(None)
    body = payloads.UpdateConfigBody(config={})
    body.env_vars = {'SKYPILOT_USER_ID': 'user-2', 'SKYPILOT_USER': 'u2'}

    with mock.patch('sky.workspaces.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock) as mock_schedule:
        await workspaces_server.update_config(request, body)

    assert mock_schedule.await_count == 2
    for call in mock_schedule.await_args_list:
        assert call.kwargs['auth_user'] is None
    check_body = mock_schedule.await_args_list[1].kwargs['request_body']
    assert check_body.env_vars['SKYPILOT_USER_ID'] == 'user-2'


@pytest.mark.asyncio
async def test_check_scheduling_failure_does_not_fail_the_save():
    """The save is already queued when the check is scheduled. A failure
    scheduling the check must not surface as a failed save."""
    request = _fake_request(models.User(id='user-3', name='u3'))
    body = payloads.UpdateConfigBody(config={})
    body.env_vars = {'SKYPILOT_USER_ID': 'user-3', 'SKYPILOT_USER': 'u3'}

    calls = []

    async def schedule(**kwargs):
        calls.append(kwargs['request_name'])
        if kwargs['request_name'] == request_names.RequestName.CHECK:
            raise RuntimeError('db unavailable')

    with mock.patch('sky.workspaces.server.executor.schedule_request_async',
                    side_effect=schedule):
        # Must not raise.
        await workspaces_server.update_config(request, body)

    assert calls == [
        request_names.RequestName.WORKSPACES_UPDATE_CONFIG,
        request_names.RequestName.CHECK,
    ]


@pytest.mark.asyncio
async def test_save_scheduling_failure_propagates_and_skips_check():
    """If the save itself cannot be scheduled, the error reaches the client
    and no check is scheduled."""
    request = _fake_request(None)
    body = payloads.UpdateConfigBody(config={})
    body.env_vars = {'SKYPILOT_USER_ID': 'user-4', 'SKYPILOT_USER': 'u4'}

    with mock.patch('sky.workspaces.server.executor.schedule_request_async',
                    new_callable=mock.AsyncMock,
                    side_effect=RuntimeError('db unavailable')) as mock_sched:
        with pytest.raises(RuntimeError):
            await workspaces_server.update_config(request, body)

    assert mock_sched.await_count == 1
