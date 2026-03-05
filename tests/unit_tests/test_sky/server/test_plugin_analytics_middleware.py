"""Tests for PluginAnalyticsMiddleware in server.py."""
from unittest import mock

import fastapi
import fastapi.testclient
import pytest
import starlette.middleware.base

from sky.server.server import PluginAnalyticsMiddleware


def _make_app():
    """Create a minimal FastAPI app with PluginAnalyticsMiddleware."""
    app = fastapi.FastAPI()
    app.add_middleware(PluginAnalyticsMiddleware)

    @app.get('/plugins/api/gpu_healer/health')
    def plugin_health():
        return {'status': 'ok'}

    @app.get('/plugins/api/gpu_healer/nodes')
    def plugin_nodes():
        return {'nodes': []}

    @app.get('/api/v1/status')
    def api_status():
        return {'status': 'running'}

    @app.get('/plugins/simple')
    def plugin_simple():
        return {'ok': True}

    return app


@pytest.fixture
def mock_posthog(monkeypatch):
    """Mock the posthog_lib used by the middleware."""
    mock_ph = mock.MagicMock()
    # Import usage_lib to monkeypatch its posthog_lib attribute.
    from sky.usage import usage_lib
    monkeypatch.setattr(usage_lib, 'posthog_lib', mock_ph)
    return mock_ph


@pytest.fixture
def _enable_logging(monkeypatch):
    """Ensure usage collection is enabled."""
    monkeypatch.delenv('SKYPILOT_DISABLE_USAGE_COLLECTION', raising=False)


class TestPluginAnalyticsMiddleware:
    """Tests for PluginAnalyticsMiddleware."""

    def test_captures_plugin_api_call(self, mock_posthog, _enable_logging):
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/plugins/api/gpu_healer/health')

        assert response.status_code == 200
        mock_posthog.capture.assert_called_once()
        call_kwargs = mock_posthog.capture.call_args[1]
        assert call_kwargs['event'] == 'plugin_api_call'
        props = call_kwargs['properties']
        assert props['source'] == 'server'
        assert props['plugin'] == 'gpu_healer'
        assert props['endpoint'] == '/plugins/api/gpu_healer/health'
        assert props['method'] == 'GET'
        assert props['status_code'] == 200

    def test_ignores_non_plugin_routes(self, mock_posthog, _enable_logging):
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/api/v1/status')

        assert response.status_code == 200
        mock_posthog.capture.assert_not_called()

    def test_extracts_plugin_name_from_url(self, mock_posthog, _enable_logging):
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/plugins/api/gpu_healer/nodes')

        assert response.status_code == 200
        props = mock_posthog.capture.call_args[1]['properties']
        assert props['plugin'] == 'gpu_healer'

    def test_respects_disable_flag(self, mock_posthog, monkeypatch):
        monkeypatch.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', '1')
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/plugins/api/gpu_healer/health')

        assert response.status_code == 200
        mock_posthog.capture.assert_not_called()

    def test_does_not_break_on_posthog_failure(self, mock_posthog,
                                               _enable_logging):
        mock_posthog.capture.side_effect = Exception('PostHog down')
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/plugins/api/gpu_healer/health')

        # Request still succeeds despite PostHog failure.
        assert response.status_code == 200
        assert response.json() == {'status': 'ok'}

    def test_handles_short_plugin_path(self, mock_posthog, _enable_logging):
        """Test /plugins/simple where there are only 2 path parts."""
        app = _make_app()
        client = fastapi.testclient.TestClient(app)

        response = client.get('/plugins/simple')

        assert response.status_code == 200
        props = mock_posthog.capture.call_args[1]['properties']
        # /plugins/simple → parts=['plugins','simple'], len=2, so 'unknown'
        assert props['plugin'] == 'unknown'
