"""CSP nonce generation and HTML injection utilities.

Provides helpers for per-request Content Security Policy nonces that
replace 'unsafe-inline' in the script-src directive.
"""
import base64
import os
import re


def generate_nonce() -> str:
    """Generate a cryptographically random base64-encoded nonce (128 bits)."""
    return base64.b64encode(os.urandom(16)).decode('ascii')


def _inject_nonce_into_tags(html: str, nonce: str) -> str:
    """Inject nonce into top-level <script> and <style> HTML tags.

    Walks the HTML string and only modifies opening tags that appear
    outside of any existing <script> or <style> block, avoiding false
    matches on literal ``<script`` / ``<style`` strings that appear
    inside minified JavaScript bundles.
    """
    result: list[str] = []
    pos = 0
    length = len(html)
    # Regex for an opening <script ...> or <style ...> tag.
    tag_open = re.compile(r'<(script|style)(\b[^>]*)>', re.IGNORECASE)

    while pos < length:
        m = tag_open.search(html, pos)
        if m is None:
            result.append(html[pos:])
            break

        tag_name = m.group(1).lower()  # "script" or "style"
        attrs = m.group(2)

        # For <script>, skip external scripts (those with src=).
        skip_nonce = (tag_name == 'script' and
                      re.search(r'\bsrc\s*=', attrs) is not None)

        # Emit everything before this tag as-is.
        result.append(html[pos:m.start()])

        if skip_nonce:
            result.append(m.group(0))
        else:
            result.append(f'<{m.group(1)} nonce="{nonce}"{attrs}>')

        # Find the matching closing tag and emit the block verbatim,
        # so we never inspect the *content* of a <script>/<style> block.
        close_tag = f'</{tag_name}>'
        close_idx = html.find(close_tag, m.end())
        if close_idx == -1:
            # Malformed HTML — no closing tag found; emit rest as-is.
            result.append(html[m.end():])
            pos = length
        else:
            end = close_idx + len(close_tag)
            result.append(html[m.end():end])
            pos = end

    return ''.join(result)


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
    html = _inject_nonce_into_tags(html, nonce)

    # Inject a <meta> tag into <head> so client JS can read the nonce.
    html = html.replace(
        '<head>',
        f'<head><meta name="csp-nonce" content="{nonce}">',
        1,  # Replace only the first occurrence.
    )

    return html
