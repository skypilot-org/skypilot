"""Infra page: what it lists, the workspace selector, Refresh and context
details.

The suite's server has an empty kubeconfig and allows only Kubernetes, so by
default nothing is enabled and the page shows its setup hint. Tests that need
a Kubernetes row use ``infra_context``: a kubeconfig context whose API server
refuses connections, with Kubernetes recorded as enabled by an earlier check.
That is a real server state (a cluster that went away), so the page renders
it through the real endpoints: the context is listed, its node query fails,
and a Refresh re-runs ``sky check``, which disables it.

GPU and node numbers need a reachable cluster; ``res`` twists stand in for
wrong ones. Everything here reads or changes server-wide infra state, so every
test is ``exclusive``.
"""
import json
import re
import time
from typing import Any, Dict, List
import urllib.parse

from framework.twists import run_script
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import BrowserContext
from playwright.sync_api import expect
from playwright.sync_api import Page
from playwright.sync_api import Route
import pytest
from seed_volumes import check_cache_restored  # pylint: disable=unused-import
from seed_volumes import infra_context  # pylint: disable=unused-import
from seed_volumes import make_workspace  # pylint: disable=unused-import

BATCH = '/enabled_clouds/batch'
NODE_INFO = '/kubernetes_node_info'
CHECK = '/check'
HINT = 'No Infrastructure Enabled'


def _path(url: str) -> str:
    return urllib.parse.urlparse(url).path


def _query(url: str) -> Dict[str, List[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def _requests(page: Page, method: str, path: str, since: int = 0) -> List[Any]:
    return [
        r for r in page.recorder.requests[since:]
        if r.method == method and _path(r.url).endswith(path)
    ]


def _wait_requests(page: Page,
                   method: str,
                   path: str,
                   since: int = 0,
                   what: str = '',
                   timeout_s: float = 30) -> List[Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reqs = _requests(page, method, path, since)
        if reqs:
            return reqs
        page.wait_for_timeout(200)
    raise AssertionError(what or f'the page sent no {method} {path}')


def _expanded_batch(page: Page, since: int = 0) -> Dict[str, List[str]]:
    """Query of the enabled-clouds batch that lists contexts (expand=true)."""
    reqs = _wait_requests(page,
                          'GET',
                          BATCH,
                          since,
                          what='the Infra page sent no enabled-clouds batch')
    expanded = [r for r in reqs if _query(r.url).get('expand') == ['true']]
    assert expanded, ('the Infra page must ask for expanded infra '
                      f'(expand=true), got {[r.url for r in reqs]}')
    return _query(expanded[-1].url)


def _context_row(page: Page, context: str):
    return page.locator('tbody tr').filter(
        has=page.get_by_text(context, exact=True)).first


def _wait_loaded(page: Page) -> None:
    """Wait for the page's own Loading... indicator to clear."""
    expect(page.get_by_text('Loading...', exact=True),
           'the Infra page must finish loading').to_have_count(0,
                                                               timeout=60_000)


class _EventLog:
    """Order in which requests start and finish, from now on."""

    def __init__(self, page: Page):
        self.events: List[tuple] = []
        page.on('request', lambda r: self.events.append(('start', r)))
        page.on('requestfinished', lambda r: self.events.append(('done', r)))

    def index(self, kind: str, pred) -> int:
        for i, (k, r) in enumerate(self.events):
            if k == kind and pred(r):
                return i
        return -1


def _check_waits_before_reload(page: Page, log: _EventLog) -> None:
    """Refresh must finish sky check before it re-reads enabled clouds:
    reading first returns the verdict the check is about to replace."""
    deadline = time.time() + 30
    check_rid = None
    while time.time() < deadline and check_rid is None:
        for kind, r in log.events:
            if kind == 'start' and r.method == 'POST' and _path(
                    r.url).endswith(CHECK):
                resp = r.response()
                if resp is not None:
                    check_rid = resp.headers.get('x-skypilot-request-id')
        if check_rid is None:
            page.wait_for_timeout(200)
    assert check_rid, 'Refresh must run sky check (POST /check)'
    done = -1
    while time.time() < deadline and done < 0:
        done = log.index(
            'done', lambda r: _path(r.url).endswith('/api/get') and _query(
                r.url).get('request_id') == [check_rid])
        if done < 0:
            page.wait_for_timeout(200)
    assert done >= 0, 'the sky check started by Refresh never finished'
    reload_ = log.index(
        'start', lambda r: r.method == 'GET' and _path(r.url).endswith(BATCH))
    assert reload_ > done, (
        'Refresh must wait for sky check to finish before re-reading enabled '
        'clouds; it re-read them while the check was still running')


# --- twist helpers -----------------------------------------------------------


def _rewrite_get(path: str, fn):
    """req twist: rewrite the URL of GETs to ``*path``."""

    def apply(context: BrowserContext) -> None:

        def handler(route: Route) -> None:
            new_url = fn(route.request.url)
            if new_url == route.request.url:
                route.continue_()
            else:
                route.continue_(url=new_url)

        context.route(f'**{path}*', handler)

    return apply


def _drop_param(url: str, key: str) -> str:
    parts = urllib.parse.urlparse(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != key]
    return parts._replace(query=urllib.parse.urlencode(q)).geturl()


def _only_default_workspace(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    q = [(k, 'default' if k == 'workspaces' else v)
         for k, v in urllib.parse.parse_qsl(parts.query)]
    return parts._replace(query=urllib.parse.urlencode(q)).geturl()


def _rewrite_async_result(path: str, match, rewrite):
    """res twist on an async endpoint: remember the request ids of matching
    calls to ``*path``, then rewrite those results in /api/get.

    ``match(route)`` picks the calls; ``rewrite(data)`` gets and returns the
    /api/get JSON (status, return_value, ...)."""

    def apply(context: BrowserContext) -> None:
        ids = set()

        def on_call(route: Route) -> None:
            resp = route.fetch()
            if match(route):
                ids.add(resp.headers.get('x-skypilot-request-id'))
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in ids:
                route.continue_()
                return
            resp = route.fetch()
            try:
                data = resp.json()
            except Exception:  # pylint: disable=broad-except
                data = {}
            route.fulfill(status=200, json=rewrite(data.get('detail', data)))

        context.route(f'**{path}*', on_call)
        context.route('**/api/get?*', on_get)

    return apply


_HEALTHY_NODES = {
    'node_info_dict': {
        'gpu-node-1': {
            'name': 'gpu-node-1',
            'accelerator_type': 'H100',
            'total': {
                'accelerator_count': 8
            },
            'free': {
                'accelerators_available': 8
            },
            'cpu_count': 96,
            'memory_gb': 1024,
            'is_ready': True,
        }
    }
}


def _node_info_reports_nodes(data: Dict[str, Any]) -> Dict[str, Any]:
    """The node query for an unreachable context succeeds with made-up
    nodes."""
    return {
        **data, 'status': 'SUCCEEDED',
        'return_value': json.dumps(_HEALTHY_NODES),
        'error': None
    }


def _all_workspaces_get_context(data: Dict[str, Any]) -> Dict[str, Any]:
    """The batch reports Kubernetes contexts for every workspace, including
    ones where Kubernetes is not enabled."""
    value = json.loads(data.get('return_value') or '{}')
    k8s = sorted({
        c for clouds in value.values() for c in clouds
        if c.lower().startswith('kubernetes/')
    })
    value = {ws: sorted(set(clouds) | set(k8s)) for ws, clouds in value.items()}
    return {**data, 'return_value': json.dumps(value)}


def _hide_button(text: str):
    """dom twist: keep hiding the button whose text is ``text``. Hidden, not
    removed: the Infra page re-renders its header and crashes when React
    finds a node it owns gone."""
    return run_script(f"""
      (() => {{
        const hide = () => document.querySelectorAll('button').forEach(b => {{
          if (b.textContent.trim() === {json.dumps(text)}) b.style.display = 'none';
        }});
        new MutationObserver(hide).observe(document, {{ childList: true, subtree: true }});
      }})();
    """)


def _is_expanded_batch(route: Route) -> bool:
    return _query(route.request.url).get('expand') == ['true']


# --- tests -------------------------------------------------------------------


@with_twists(
    Twist('src-infra_volumes-enabled-clouds-unchecked',
          'src',
          expect=r'with nothing enabled the Infra page must show only the '
          r'setup hint',
          patch='twist_patches/infra_volumes-enabled-clouds-unchecked.patch'),
    Twist('req-infra-batch-not-expanded',
          'req',
          expect=r'must ask for expanded infra',
          apply=_rewrite_get(BATCH, lambda u: _drop_param(u, 'expand'))),
    Twist('dom-infra-no-refresh',
          'dom',
          expect=r'the Infra page must offer Refresh',
          apply=_hide_button('Refresh')),
)
@pytest.mark.exclusive
def test_infra_without_enabled_infra_shows_setup_hint(make_user, open_as,
                                                      check_cache_restored,
                                                      twist):
    viewer = make_user('viewer')
    page = open_as(viewer, '/dashboard/infra')
    _wait_loaded(page)

    query = _expanded_batch(page)
    assert 'default' in query.get('workspaces', [''])[0].split(','), (
        'the enabled-clouds batch must ask for the default workspace, '
        f'got {query}')
    _wait_requests(page,
                   'POST',
                   '/slurm_cluster_names',
                   what='the Infra page must look up Slurm clusters')
    expect(
        page.get_by_role('heading', name=HINT),
        'with nothing enabled the Infra page must show only the setup '
        'hint').to_be_visible()
    for section in ('Kubernetes', 'Slurm', 'Cloud', 'SSH Node Pool'):
        expect(
            page.get_by_role('heading', name=section, exact=True),
            'with nothing enabled the Infra page must show only the setup '
            f'hint, not the {section} section').to_have_count(0)
    assert not _requests(page, 'POST', NODE_INFO), (
        'with no Kubernetes context the page must not query any context')
    expect(page.get_by_role('combobox'),
           'the workspace selector must start on All Workspaces').to_have_text(
               'All Workspaces')

    refresh = page.get_by_role('button', name='Refresh')
    expect(refresh, 'the Infra page must offer Refresh').to_be_visible()
    since = page.recorder.mark()
    refresh.click()
    _wait_requests(page,
                   'POST',
                   CHECK,
                   since,
                   what='Refresh must run sky check (POST /check)')
    _expanded_batch(page, since)
    _wait_loaded(page)
    expect(
        page.get_by_role('heading', name=HINT),
        'with nothing enabled the Infra page must show only the setup '
        'hint after Refresh').to_be_visible()


@with_twists(
    Twist('srv-infra_volumes-check-narrowed-to-ssh',
          'srv',
          expect=r'after Refresh, sky check must drop the unreachable '
          r'context'),
    Twist('src-infra_volumes-refresh-check-not-awaited',
          'src',
          expect=r'Refresh must wait for sky check to finish',
          patch='twist_patches/infra_volumes-refresh-check-not-awaited.patch'),
    Twist('res-infra-unreachable-context-has-nodes',
          'res',
          expect=r'an unreachable context must not report',
          apply=_rewrite_async_result(NODE_INFO, lambda r: True,
                                      _node_info_reports_nodes)),
    Twist('dom-infra-refresh-does-nothing',
          'dom',
          expect=r'Refresh must run sky check',
          apply=swallow_click('Refresh')),
)
@pytest.mark.exclusive
def test_kubernetes_context_listed_until_refresh_rechecks_it(
        make_user, open_as, infra_context, twist):
    context = infra_context
    viewer = make_user('viewer')
    page = open_as(viewer, '/dashboard/infra')
    _wait_loaded(page)

    query = _expanded_batch(page)
    assert 'default' in query.get('workspaces', [''])[0].split(','), (
        f'the enabled-clouds batch must ask for the default workspace, '
        f'got {query}')
    bodies = [
        json.loads(r.post_data or '{}')
        for r in _wait_requests(page,
                                'POST',
                                NODE_INFO,
                                what='the Infra page must query each '
                                'enabled context (POST /kubernetes_node_info)')
    ]
    assert any(b.get('context') == context for b in bodies), (
        f'the node query must name the context {context}, got {bodies}')

    expect(page.get_by_text('1 context', exact=True),
           'the Kubernetes section must count one context').to_be_visible()
    headers = page.locator('thead').first.locator('th')
    expect(
        headers,
        'the Kubernetes table must have its capacity columns').to_contain_text([
            'Name', 'Nodes', 'CPU', 'Memory', 'GPU Type', 'GPUs', 'Utilization'
        ])
    row = _context_row(page, context)
    expect(row, f'context {context} must be listed').to_be_visible()
    cells = row.locator('td')
    expect(cells.nth(2),
           'an unreachable context must not report nodes').to_have_text('0')
    for i, col in ((5, 'GPU Type'), (6, 'GPUs'), (7, 'Utilization')):
        expect(
            cells.nth(i),
            f'an unreachable context must not report {col}').to_have_text('-')
    expect(page.get_by_role('heading', name='Cloud', exact=True),
           'the other infra sections must still be shown').to_be_visible()
    expect(page.get_by_text('No enabled clouds available.'),
           'the Cloud section must say no cloud is enabled').to_be_visible()

    log = _EventLog(page)
    since = page.recorder.mark()
    page.get_by_role('button', name='Refresh').click()
    _wait_requests(page,
                   'POST',
                   CHECK,
                   since,
                   what='Refresh must run sky check (POST /check)',
                   timeout_s=10)
    _check_waits_before_reload(page, log)
    expect(
        page.get_by_role('heading', name=HINT),
        'after Refresh, sky check must drop the unreachable context and '
        'leave the setup hint').to_be_visible()
    expect(page.get_by_text(context, exact=True),
           'after Refresh, sky check must drop the unreachable context'
          ).to_have_count(0)


@with_twists(
    Twist('res-infra-detail-unreachable-has-nodes',
          'res',
          expect=r'an unreachable context must list no nodes',
          apply=_rewrite_async_result(NODE_INFO, lambda r: True,
                                      _node_info_reports_nodes)),
    Twist('dom-infra-context-link-dead',
          'dom',
          expect=r'clicking a context must open its detail page',
          apply=run_script("""
            document.addEventListener('click', (e) => {
              const t = e.target.closest('td span');
              if (t && /-ctx$/.test(t.innerText.trim())) {
                e.stopImmediatePropagation();
                e.preventDefault();
              }
            }, true);
          """)),
)
@pytest.mark.exclusive
def test_context_detail_page(make_user, open_as, infra_context, twist):
    context = infra_context
    viewer = make_user('viewer')
    page = open_as(viewer, '/dashboard/infra')
    _wait_loaded(page)

    row = _context_row(page, context)
    expect(row, f'context {context} must be listed').to_be_visible()
    row.get_by_text(context, exact=True).click()
    expect(page, 'clicking a context must open its detail page').to_have_url(
        re.compile(rf'/dashboard/infra/{re.escape(context)}$'))
    expect(
        page.get_by_role('heading', name=context, level=1),
        'the detail page must be titled with the context name').to_be_visible()
    expect(page.get_by_role('combobox'),
           'the detail page must not show the list-view workspace selector'
          ).to_have_count(0)
    expect(page.get_by_text('No nodes found'),
           'an unreachable context must list no nodes').to_be_visible()

    page.get_by_role('link', name='← Infrastructure').click()
    expect(page, 'the back link must return to the Infra list').to_have_url(
        re.compile(r'/dashboard/infra/?$'))
    expect(_context_row(page, context),
           'the Infra list must show the context again').to_be_visible()


@with_twists(
    Twist('req-infra-batch-only-default-workspace',
          'req',
          expect=r'the enabled-clouds batch must ask for every workspace',
          apply=_rewrite_get(BATCH, _only_default_workspace)),
    Twist('res-infra-context-in-every-workspace',
          'res',
          expect=r'a workspace without Kubernetes must not list',
          apply=_rewrite_async_result(BATCH, _is_expanded_batch,
                                      _all_workspaces_get_context)),
)
@pytest.mark.exclusive
def test_workspace_selector_filters_contexts(make_user, open_as, infra_context,
                                             make_workspace, uid, twist):
    context = infra_context
    ws_id = make_workspace('ws')
    ws_name = f'{uid}-ws'
    viewer = make_user('viewer')
    page = open_as(viewer, '/dashboard/infra')
    _wait_loaded(page)

    query = _expanded_batch(page)
    asked = query.get('workspaces', [''])[0].split(',')
    assert 'default' in asked and ws_id in asked, (
        'the enabled-clouds batch must ask for every workspace '
        f'(default and {ws_id}), got {asked}')
    expect(_context_row(page, context),
           'All Workspaces must list the default workspace\'s context'
          ).to_be_visible()

    page.get_by_role('combobox').click()
    page.get_by_role('option', name=ws_name, exact=True).click()
    expect(page, 'choosing a workspace must put it in the URL').to_have_url(
        re.compile(rf'[?&]workspace={re.escape(ws_id)}'))
    expect(page.get_by_role('combobox'),
           'the selector must show the chosen workspace').to_have_text(ws_name)
    expect(page.get_by_text(context, exact=True),
           f'a workspace without Kubernetes must not list {context}'
          ).to_have_count(0)
    expect(page.get_by_role('heading', name=HINT),
           'a workspace with nothing enabled must show the setup hint'
          ).to_be_visible()

    page.get_by_role('combobox').click()
    page.get_by_role('option', name='default', exact=True).click()
    expect(page, 'choosing default must put it in the URL').to_have_url(
        re.compile(r'[?&]workspace=default'))
    expect(_context_row(page, context),
           'the default workspace must list its context').to_be_visible()
