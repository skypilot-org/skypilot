"""Twists: deliberate regressions that prove each e2e test can fail.

Every test declares its twists with ``@with_twists(...)``. Pytest then runs the
test once as ``baseline`` (must pass) and once per twist. A twist run passes
only if the test body fails *and* the failure message matches the twist's
``expect`` pattern (see ``pytest_pyfunc_call`` in fixtures.py). A twist that
leaves the test green, or makes it fail for another reason, fails the run.

Layers:

- ``req``: rewrite a request the browser sends, before it reaches the server.
- ``res``: rewrite a server response before the page sees it.
- ``dom``: change the rendered page (remove an element, swallow a click).
- ``srv``: ask the server to misbehave for this request only, through the
  ``X-E2E-Twist`` header served by ``server_plugin.py``.
- ``src``: a real source patch. The suite runs it only when the build carries
  that patch (``E2E_SRC_TWIST=<id>``); otherwise it is skipped.

``req``, ``res``, ``dom`` and ``srv`` only touch the twisted test's own browser
context, so twisted and baseline runs share one server and run in parallel.
"""

import dataclasses
import json
import os
import re
from typing import Any, Callable, Dict, Optional

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route
import pytest

SRC_TWIST_ENV = 'E2E_SRC_TWIST'
LAYERS = ('req', 'res', 'dom', 'srv', 'src')


@dataclasses.dataclass(frozen=True)
class Twist:
    id: str
    layer: str
    # Regex the failure message must match: name the assertion that should
    # fire, not just "Timeout".
    expect: str
    apply: Optional[Callable[[BrowserContext], None]] = None
    # src twists: the patch a CI job applies before building and starting the
    # server, relative to the test suite directory.
    patch: Optional[str] = None

    def __post_init__(self):
        assert self.layer in LAYERS, self.layer
        assert (self.layer == 'src') == (self.patch is not None), self.id
        if self.patch is not None:
            # CI finds src twists by walking twist_patches/, so the id must
            # name the file: twist_patches/<x>.patch <-> id 'src-<x>'.
            stem = os.path.splitext(os.path.basename(self.patch))[0]
            assert self.id == f'src-{stem}', f'{self.id} must be named src-{stem}'

    def __str__(self) -> str:
        return self.id


def with_twists(*twists: Twist):
    """Parametrize a test as ``baseline`` plus one run per twist."""
    assert twists, 'every e2e test needs at least one twist'
    active_src = os.environ.get(SRC_TWIST_ENV)
    params = []
    if active_src:
        # The build carries a source patch: baseline runs would be meaningless.
        params.append(
            pytest.param(
                None,
                id='baseline',
                marks=pytest.mark.skip(
                    reason=f'build carries src twist {active_src}'),
            ))
    else:
        params.append(pytest.param(None, id='baseline'))
    for t in twists:
        marks = []
        if t.layer == 'src' and active_src != t.id:
            marks.append(
                pytest.mark.skip(
                    reason=f'src twist {t.id} needs a patched build'))
        elif t.layer != 'src' and active_src:
            marks.append(
                pytest.mark.skip(
                    reason=f'build carries src twist {active_src}'))
        params.append(pytest.param(t, id=t.id, marks=marks))
    return pytest.mark.parametrize('twist', params)


# --- req / res -----------------------------------------------------------


def _url_pattern(path_suffix: str) -> 're.Pattern[str]':
    """Match URLs whose path ends with ``path_suffix``, with or without a
    query string. A ``**suffix`` glob misses ``/api/get?request_id=...``."""
    return re.compile(re.escape(path_suffix) + r'(\?.*)?$')


def rewrite_request(path_suffix: str, fn: Callable[[Dict[str, Any]],
                                                   Dict[str, Any]]):
    """req twist: rewrite the JSON body of POSTs to ``*path_suffix``."""

    def apply(context: BrowserContext) -> None:

        def handler(route: Route) -> None:
            req = route.request
            if req.method != 'POST' or not req.post_data:
                route.continue_()
                return
            body = json.loads(req.post_data)
            route.continue_(post_data=json.dumps(fn(body)))

        context.route(_url_pattern(path_suffix), handler)

    return apply


def rewrite_response(path_suffix: str, fn: Callable[[Any, Dict[str, Any]],
                                                    Any]):
    """res twist: rewrite the JSON response of ``*path_suffix``.

    ``fn(data, request_json)`` gets the parsed response and the request body.
    """

    def apply(context: BrowserContext) -> None:

        def handler(route: Route) -> None:
            try:
                resp = route.fetch()
            except PlaywrightError:
                # The context closed while the request was in flight (e.g. an
                # /api/get long-poll at teardown). Raising here would surface
                # as TargetClosedError in the next test on this worker.
                return
            try:
                data = resp.json()
            except Exception:  # pylint: disable=broad-except
                route.fulfill(response=resp)
                return
            body = json.loads(route.request.post_data or '{}')
            try:
                route.fulfill(response=resp, json=fn(data, body))
            except PlaywrightError:
                return

        context.route(_url_pattern(path_suffix), handler)

    return apply


# --- dom -------------------------------------------------------------------


def remove_element(css: str):
    """dom twist: keep removing every element matching ``css``."""
    script = f"""
      (() => {{
        const sel = {json.dumps(css)};
        const strip = () => document.querySelectorAll(sel).forEach(e => e.remove());
        new MutationObserver(strip).observe(document, {{ childList: true, subtree: true }});
      }})();
    """

    def apply(context: BrowserContext) -> None:
        context.add_init_script(script)

    return apply


def remove_button(text: str):
    """dom twist: keep removing the button whose text is ``text``."""
    script = f"""
      (() => {{
        const strip = () => document.querySelectorAll('button').forEach(b => {{
          if (b.innerText.trim() === {json.dumps(text)}) b.remove();
        }});
        new MutationObserver(strip).observe(document, {{ childList: true, subtree: true }});
      }})();
    """

    def apply(context: BrowserContext) -> None:
        context.add_init_script(script)

    return apply


def swallow_click(text: str):
    """dom twist: clicks on the button whose text is ``text`` do nothing."""
    script = f"""
      document.addEventListener('click', (e) => {{
        const b = e.target.closest('button');
        if (b && b.innerText.trim() === {json.dumps(text)}) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """

    def apply(context: BrowserContext) -> None:
        context.add_init_script(script)

    return apply


def run_script(js: str):
    """dom twist: run arbitrary page script on every document."""

    def apply(context: BrowserContext) -> None:
        context.add_init_script(js)

    return apply
