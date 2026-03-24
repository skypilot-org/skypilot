"""Unit tests for the SecurityHeadersMiddleware."""

import fastapi
import fastapi.testclient

from sky.server.server import SecurityHeadersMiddleware


def _make_app():
    """Create a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = fastapi.FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get('/test')
    def test_endpoint():
        return {'status': 'ok'}

    @app.get('/dashboard/index.html')
    def dashboard_endpoint():
        return fastapi.responses.HTMLResponse('<html></html>')

    return app


class TestSecurityHeadersMiddleware:
    """Tests for SecurityHeadersMiddleware."""

    def setup_method(self):
        self.app = _make_app()
        self.client = fastapi.testclient.TestClient(self.app)

    def test_csp_header_present(self):
        """CSP should be in enforcing mode."""
        response = self.client.get('/test')
        assert 'Content-Security-Policy' in response.headers

    def test_csp_policy_directives(self):
        """CSP policy should contain all required directives."""
        response = self.client.get('/test')
        csp = response.headers['Content-Security-Policy']
        assert 'default-src \'self\'' in csp
        assert 'script-src \'self\' \'unsafe-inline\'' in csp
        assert 'style-src \'self\' \'unsafe-inline\'' in csp
        assert 'object-src \'none\'' in csp
        assert 'frame-ancestors \'self\'' in csp
        assert 'img-src \'self\' data:' in csp
        assert 'base-uri \'self\'' in csp

    def test_csp_allows_localhost_connect(self):
        """CSP connect-src must allow localhost for legacy auth callback."""
        response = self.client.get('/test')
        csp = response.headers['Content-Security-Policy']
        assert 'http://localhost:*' in csp
        assert 'http://127.0.0.1:*' in csp

    def test_x_frame_options_header(self):
        response = self.client.get('/test')
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'

    def test_x_content_type_options_header(self):
        response = self.client.get('/test')
        assert response.headers['X-Content-Type-Options'] == 'nosniff'

    def test_referrer_policy_header(self):
        response = self.client.get('/test')
        assert (response.headers['Referrer-Policy'] ==
                'strict-origin-when-cross-origin')

    def test_permissions_policy_header(self):
        response = self.client.get('/test')
        assert (response.headers['Permissions-Policy'] ==
                'camera=(), microphone=(), geolocation=()')

    def test_headers_on_dashboard_routes(self):
        """Security headers should also apply to dashboard HTML responses."""
        response = self.client.get('/dashboard/index.html')
        assert 'Content-Security-Policy' in response.headers
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'

    def test_headers_on_json_api_routes(self):
        """Security headers should apply to API JSON responses."""
        response = self.client.get('/test')
        assert response.status_code == 200
        assert 'Content-Security-Policy' in response.headers
