"""CSP nonce generation and HTML injection utilities.

Provides helpers for per-request Content Security Policy nonces that
replace 'unsafe-inline' in script-src and style-src directives.
"""
import base64
import os
import re


def generate_nonce() -> str:
    """Generate a cryptographically random base64-encoded nonce (128 bits)."""
    return base64.b64encode(os.urandom(16)).decode('ascii')


def inject_nonce_into_html(html: str, nonce: str) -> str:
    """Inject nonce attributes into inline <script> and <style> tags.

    Also injects a <meta name="csp-nonce"> tag into <head> so that
    client-side JavaScript (e.g. Emotion CacheProvider) can read the
    nonce for dynamically created style elements.

    Args:
        html: The HTML content to modify.
        nonce: The base64-encoded nonce value.

    Returns:
        The modified HTML with nonce attributes injected.
    """
    # Add nonce to inline <script> tags (those WITHOUT a src= attribute).
    # The negative lookahead (?![^>]*\bsrc\s*=) skips external scripts
    # which are already covered by 'self' in the CSP.
    html = re.sub(
        r'<script(?![^>]*\bsrc\s*=)([^>]*)>',
        rf'<script nonce="{nonce}"\1>',
        html,
    )

    # Add nonce to <style> tags.
    html = re.sub(
        r'<style([^>]*)>',
        rf'<style nonce="{nonce}"\1>',
        html,
    )

    # Inject a <meta> tag into <head> so client JS can read the nonce.
    html = html.replace(
        '<head>',
        f'<head><meta name="csp-nonce" content="{nonce}">',
        1,  # Replace only the first occurrence.
    )

    return html
