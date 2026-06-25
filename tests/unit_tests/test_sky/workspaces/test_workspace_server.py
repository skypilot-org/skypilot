"""Unit tests for workspace REST handlers."""

import types
from unittest import mock

import pytest

from sky.server.requests import payloads
from sky.workspaces import server as workspaces_server


@pytest.mark.asyncio
async def test_update_config_rejects_when_dashboard_config_editor_disabled(
        monkeypatch):
    monkeypatch.setattr(
        workspaces_server.skypilot_config, 'get_nested',
        lambda keys, default: True
        if keys == ('dashboard', 'disable_config_editor') else default)
    schedule_mock = mock.AsyncMock()
    monkeypatch.setattr(workspaces_server.executor, 'schedule_request_async',
                        schedule_mock)
    request = types.SimpleNamespace(
        state=types.SimpleNamespace(request_id='req-123', auth_user=None))
    body = payloads.UpdateConfigBody(config={'workspaces': {'default': {}}})

    with pytest.raises(workspaces_server.fastapi.HTTPException) as exc_info:
        await workspaces_server.update_config(request, body)

    assert exc_info.value.status_code == 403
    assert 'config editing is disabled' in exc_info.value.detail
    schedule_mock.assert_not_called()


@pytest.mark.asyncio
async def test_update_config_schedules_when_dashboard_config_editor_enabled(
        monkeypatch):
    monkeypatch.setattr(
        workspaces_server.skypilot_config, 'get_nested',
        lambda keys, default: False
        if keys == ('dashboard', 'disable_config_editor') else default)
    schedule_mock = mock.AsyncMock()
    monkeypatch.setattr(workspaces_server.executor, 'schedule_request_async',
                        schedule_mock)
    request = types.SimpleNamespace(
        state=types.SimpleNamespace(request_id='req-123', auth_user='user'))
    body = payloads.UpdateConfigBody(config={'workspaces': {'default': {}}})

    response = await workspaces_server.update_config(request, body)

    assert response is None
    schedule_mock.assert_awaited_once()
