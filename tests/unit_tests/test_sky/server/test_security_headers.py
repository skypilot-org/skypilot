"""Unit tests for the SecurityHeadersMiddleware."""

import re

import fastapi
import fastapi.testclient

from sky.server import csp_utils
from sky.server.server import SecurityHeadersMiddleware


def _make_app():
    """Create a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = fastapi.FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get('/test')
    def test_endpoint():
        return {'status': 'ok'}

    @app.get('/dashboard/index.html')
    def dashboard_endpoint(request: fastapi.Request):
        """Simulate a nonced HTML response like serve_dashboard()."""
        nonce = csp_utils.generate_nonce()
        request.state.csp_nonce = nonce
        html = csp_utils.inject_nonce_into_html(
            '<html><head></head><body>'
            '<script>var x=1;</script>'
            '</body></html>',
            nonce,
        )
        return fastapi.responses.HTMLResponse(html)

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

    def test_csp_no_unsafe_inline_in_script_src(self):
        """script-src must not allow unsafe-inline."""
        response = self.client.get('/test')
        csp = response.headers['Content-Security-Policy']
        script_src = re.search(r"script-src ([^;]+)", csp).group(1)
        assert 'unsafe-inline' not in script_src
        assert 'unsafe-eval' not in csp

    def test_csp_strict_policy_on_non_html(self):
        """Non-HTML responses should have a strict self-only policy."""
        response = self.client.get('/test')
        csp = response.headers['Content-Security-Policy']
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self' 'unsafe-inline'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'self'" in csp
        assert "img-src 'self' data:" in csp
        assert "base-uri 'self'" in csp

    def test_csp_nonce_on_html_response(self):
        """HTML responses should use nonce-based CSP."""
        response = self.client.get('/dashboard/index.html')
        csp = response.headers['Content-Security-Policy']
        # script-src must use nonce, not unsafe-inline.
        script_src = re.search(r"script-src ([^;]+)", csp).group(1)
        assert 'unsafe-inline' not in script_src
        # Extract the nonce from the CSP header.
        match = re.search(r"'nonce-([A-Za-z0-9+/=]+)'", csp)
        assert match is not None, f'No nonce found in CSP: {csp}'
        nonce = match.group(1)
        assert f"script-src 'self' 'nonce-{nonce}'" in csp
        # style-src uses unsafe-inline (CSS cannot execute scripts).
        assert "style-src 'self' 'unsafe-inline'" in csp

    def test_nonce_in_html_body_matches_csp_header(self):
        """The nonce in the HTML body should match the CSP header nonce."""
        response = self.client.get('/dashboard/index.html')
        csp = response.headers['Content-Security-Policy']
        csp_nonce = re.search(r"'nonce-([A-Za-z0-9+/=]+)'", csp).group(1)
        # Check that the HTML body contains the matching nonce.
        body = response.text
        assert f'<meta name="csp-nonce" content="{csp_nonce}">' in body
        assert f'<script nonce="{csp_nonce}"' in body

    def test_nonce_differs_across_requests(self):
        """Each request should receive a unique nonce."""
        r1 = self.client.get('/dashboard/index.html')
        r2 = self.client.get('/dashboard/index.html')
        n1 = re.search(r"'nonce-([A-Za-z0-9+/=]+)'",
                       r1.headers['Content-Security-Policy']).group(1)
        n2 = re.search(r"'nonce-([A-Za-z0-9+/=]+)'",
                       r2.headers['Content-Security-Policy']).group(1)
        assert n1 != n2

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
