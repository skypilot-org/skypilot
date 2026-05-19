"""Unit tests for sky.server.csp_utils."""

import base64

from sky.server import csp_utils


class TestGenerateNonce:
    """Tests for generate_nonce()."""

    def test_returns_base64_string(self):
        nonce = csp_utils.generate_nonce()
        # Should be valid base64 — decoding should not raise.
        decoded = base64.b64decode(nonce)
        assert len(decoded) == 16  # 128 bits

    def test_unique_across_calls(self):
        nonces = {csp_utils.generate_nonce() for _ in range(100)}
        assert len(nonces) == 100


class TestInjectNonceIntoHtml:
    """Tests for inject_nonce_into_html()."""

    NONCE = 'dGVzdG5vbmNl'  # base64 of "testnonce"

    def test_adds_nonce_to_inline_script(self):
        html = '<html><head></head><body><script>alert(1)</script></body></html>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<script nonce="{self.NONCE}">' in result

    def test_does_not_add_nonce_to_external_script(self):
        html = '<script src="/app.js"></script>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'nonce="{self.NONCE}"' not in result

    def test_adds_nonce_to_style_tag(self):
        html = '<html><head><style>body{}</style></head></html>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<style nonce="{self.NONCE}">' in result

    def test_adds_meta_tag(self):
        html = '<html><head></head><body></body></html>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<meta name="csp-nonce" content="{self.NONCE}">' in result

    def test_meta_tag_only_added_once(self):
        html = '<html><head></head><body></body></html>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert result.count('csp-nonce') == 1

    def test_handles_script_with_attributes(self):
        html = '<script id="__NEXT_DATA__" type="application/json">{"a":1}</script>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<script nonce="{self.NONCE}" id="__NEXT_DATA__"' in result

    def test_preserves_external_script_src(self):
        html = ('<script src="/a.js"></script>'
                '<script>inline()</script>'
                '<script src="/b.js" defer></script>')
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        # Only the inline script should get a nonce.
        assert result.count(f'nonce="{self.NONCE}"') == 1
        assert f'<script nonce="{self.NONCE}">inline()</script>' in result

    def test_multiple_style_tags(self):
        html = '<style>a{}</style><style>b{}</style>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert result.count(f'nonce="{self.NONCE}"') == 2

    def test_no_head_tag(self):
        """Should still inject nonce on script/style even without <head>."""
        html = '<script>x()</script>'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<script nonce="{self.NONCE}">x()</script>' in result
        # No meta tag since there is no <head> to inject into.
        assert 'csp-nonce' not in result

    def test_no_false_positive_script_inside_js(self):
        """Nonce must not be injected into <script> literals in JS code.

        Minified bundles (e.g. Vite/React) may contain strings like
        ``innerHTML="<script><\\/script>"`` which should not be treated
        as real HTML tags.
        """
        html = ('<script type="module">'
                'e.innerHTML="<script><\\/script>";'
                '</script>')
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        # Only the outer <script> should get a nonce.
        assert result.count(f'nonce="{self.NONCE}"') == 1
        assert f'<script nonce="{self.NONCE}" type="module">' in result

    def test_no_false_positive_style_inside_js(self):
        """Nonce must not be injected into <style> literals in JS code."""
        html = ('<script>'
                'el.innerHTML="<style>body{color:red}</style>";'
                '</script>')
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        # Only the <script> gets a nonce; the <style> inside JS does not.
        assert result.count(f'nonce="{self.NONCE}"') == 1
        assert f'<script nonce="{self.NONCE}">' in result

    def test_unclosed_script_tag(self):
        """Malformed HTML with no closing </script> should not crash."""
        html = '<script>alert(1)'
        result = csp_utils.inject_nonce_into_html(html, self.NONCE)
        assert f'<script nonce="{self.NONCE}">' in result
        assert 'alert(1)' in result
