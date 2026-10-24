"""Clusters list and cluster detail pages.

The list asks ``POST /status`` for clusters (``all_users=false`` for "My
Clusters") and, in the "All" view, ``POST /cost_report`` for terminated
clusters within the chosen window. Filters are applied in the page and kept
in the URL. The detail page asks ``/status`` for one cluster by name and
``/queue`` for its jobs.

Tests asserting global numbers (how many clusters other users own, "1 - N of
N") are ``exclusive`` and read expected numbers from the DB, as are tests that
create workspaces (server config). The rest assert only on rows they created
and run in parallel.
"""
import json
import re
import time
from typing import Any, Callable, Dict, List

from framework import seed
from framework.twists import remove_button
from framework.twists import remove_element
from framework.twists import rewrite_request
from framework.twists import run_script
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import BrowserContext
from playwright.sync_api import expect
from playwright.sync_api import Page
from playwright.sync_api import Route
import pytest
import seed_clusters

STATUS = '/status'
COST_REPORT = '/cost_report'
QUEUE = '/queue'
COLUMNS = [
    'Status', 'Cluster', 'User', 'Workspace', 'Infra', 'Resources', 'Started',
    'Autostop', 'Actions'
]

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def _purge_after():
    """Names whose history rows and events are removed after the test. It is
    set up before ``seed_cluster``, so it tears down after the live rows are
    gone."""
    names: List[str] = []
    yield names
    for name in names:
        seed_clusters.purge(name)


@pytest.fixture
def workspace(admin_api) -> Callable[..., str]:
    """Create workspaces through the API; deleted afterwards. Workspaces are
    server config, so only exclusive tests use this."""
    created: List[str] = []

    def make(name: str, **config) -> str:
        ws_id = seed_clusters.create_workspace(admin_api, name, config)
        created.append(ws_id)
        return ws_id

    yield make
    for ws_id in created:
        seed_clusters.delete_workspace(admin_api, ws_id)


@pytest.fixture
def cluster(workspace, _purge_after, seed_cluster) -> Callable[..., str]:
    """``seed_cluster`` that also cleans up the history and events it leaves.

    It depends on ``workspace`` only for teardown order: a workspace with
    active clusters cannot be deleted, so its clusters must go first.
    ``workspace`` creates nothing unless a test calls it.
    """

    def make(*args, **kwargs) -> str:
        name = seed_cluster(*args, **kwargs)
        _purge_after.append(name)
        return name

    return make


# --- helpers -----------------------------------------------------------------


def _is_list(body: Dict[str, Any]) -> bool:
    """A clusters-table request: every cluster, not a by-name lookup."""
    return 'summary_response' in body and not body.get('cluster_names')


def _wait_post(page: Page,
               path: str,
               since: int,
               match: Callable[[Dict[str, Any]], bool],
               what: str,
               timeout_s: float = 20) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reqs = [b for b in page.recorder.posts(path, since) if match(b)]
        if reqs:
            return reqs[-1]
        page.wait_for_timeout(200)
    raise AssertionError(f'the page sent no {what} request')


def _row(page: Page, name: str):
    return page.locator('tbody tr').filter(
        has=page.get_by_role('link', name=name, exact=True))


def _headers(page: Page) -> List[str]:
    page.locator('thead th').first.wait_for()
    return [
        re.sub(r'\s*[↑↓]$', '', t).strip()
        for t in page.locator('thead th').all_inner_texts()
    ]


def _tab(page: Page, group: str, name: str):
    return page.get_by_role('tablist', name=group).get_by_role('tab',
                                                               name=name,
                                                               exact=True)


def _rewrite_result(path: str, match: Callable[[Dict[str, Any]], bool],
                    fn: Callable[[Any, Dict[str, Any]], Any]):
    """res twist on an async request: the call to ``*path`` (a POST, or a
    GET with an empty body) returns a request id and its result comes from
    /api/get. Remember the ids of matching calls, then rewrite those results
    with ``fn(value, request_body)``."""

    def apply(context: BrowserContext) -> None:
        bodies: Dict[str, Dict[str, Any]] = {}

        def on_post(route: Route) -> None:
            resp = route.fetch()
            body = json.loads(route.request.post_data or '{}')
            if match(body):
                bodies[resp.headers.get('x-skypilot-request-id')] = body
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            resp = route.fetch()
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in bodies or resp.status != 200:
                route.fulfill(response=resp)
                return
            data = resp.json()
            value = json.loads(data['return_value'])
            data['return_value'] = json.dumps(fn(value, bodies[rid]))
            route.fulfill(response=resp, json=data)

        context.route(f'**{path}', on_post)
        context.route('**/api/get?*', on_get)

    return apply


def _edit_clusters(name_pattern: str, **changes):
    """``fn`` for _rewrite_result: change fields of the clusters whose name
    matches ``name_pattern`` (a regex)."""

    def fn(value, body):
        del body
        for c in value:
            if re.search(name_pattern, c.get('name') or ''):
                c.update(changes)
        return value

    return fn


def _drop_clusters(name_pattern: str):

    def fn(value, body):
        del body
        return [
            c for c in value if not re.search(name_pattern,
                                              c.get('name') or '')
        ]

    return fn


def _strip_url_params(*params: str):
    """dom twist: the page loses these URL parameters before it reads them,
    as if it no longer parsed them from a shared link."""
    return run_script(f"""
      (() => {{
        const url = new URL(window.location.href);
        let changed = false;
        for (const p of {json.dumps(list(params))}) {{
          if (url.searchParams.has(p)) {{ url.searchParams.delete(p); changed = true; }}
        }}
        if (changed) window.history.replaceState(window.history.state, '', url.toString());
      }})();
    """)


def _swallow_clicks(css: str):
    """dom twist: clicks inside elements matching ``css`` do nothing."""
    return run_script(f"""
      document.addEventListener('click', (e) => {{
        if (e.target.closest({json.dumps(css)})) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """)


# Value options of the "Filter clusters" dropdown.
_FILTER_OPTION = 'div.cursor-pointer.px-3'


def _pick_filter(page: Page, prop: str, value: str) -> None:
    page.get_by_role('combobox', name='Filter Property').click()
    page.get_by_role('option', name=prop, exact=True).click()
    box = page.get_by_placeholder('Filter clusters')
    box.click()
    box.fill(value)
    option = page.locator(_FILTER_OPTION).filter(
        has_text=re.compile(rf'^{re.escape(value)}$'))
    expect(option,
           f'the {prop} filter must offer the value {value}').to_be_visible()
    option.click()


def _chip(page: Page, prop: str, value: str):
    return page.locator('div.rounded-full').filter(
        has_text=re.compile(rf'^\s*{prop}\s*:\s*{re.escape(value)}\s*$'))


# --- list: scope ---------------------------------------------------------------


@with_twists(
    Twist('req-my-clusters-asks-all-users',
          'req',
          expect=r"My Clusters must ask the server for the viewer's clusters "
          r'only',
          apply=rewrite_request(
              STATUS, lambda b: {
                  **b, 'all_users': True
              } if _is_list(b) else b)),
    Twist('srv-clusters-status-ignores-scope',
          'srv',
          expect=r"another user's cluster .* must not be listed under My "
          r'Clusters'),
    Twist('src-clusters-status-ignores-scope',
          'src',
          expect=r"another user's cluster .* must not be listed under My "
          r'Clusters',
          patch='twist_patches/clusters-status-ignores-scope.patch'),
    Twist('res-status-reports-stopped',
          'res',
          expect=r'must be listed as RUNNING',
          apply=_rewrite_result(STATUS, _is_list,
                                _edit_clusters(r'-up$', status='STOPPED'))),
    Twist('dom-no-autostop-column',
          'dom',
          expect=r'the clusters table must show the columns',
          apply=run_script("""
            new MutationObserver(() => document.querySelectorAll('thead th')
              .forEach(th => { if (th.innerText.startsWith('Autostop')) th.remove(); }))
              .observe(document, { childList: true, subtree: true });
          """)),
)
def test_my_clusters_lists_only_my_clusters(make_user, cluster, open_as, uid,
                                            twist):
    owner = make_user('owner')
    other = make_user('other')
    up = cluster(f'{uid}-up', owner.user_id, status='UP')
    stopped = cluster(f'{uid}-stop', owner.user_id, status='STOPPED')
    theirs = cluster(f'{uid}-theirs', other.user_id, status='UP')

    page = open_as(owner, '/dashboard/clusters')

    _wait_post(page, STATUS, 0, _is_list, 'clusters list')
    reqs = [b for b in page.recorder.posts(STATUS) if _is_list(b)]
    assert any(b.get('all_users') is False for b in reqs), (
        'My Clusters must ask the server for the viewer\'s clusters only '
        f'(all_users=false), got {reqs}')
    expect(_tab(page, 'Filter clusters by owner', 'My Clusters'),
           'My Clusters must be the default scope').to_have_attribute(
               'aria-selected', 'true')
    expect(_row(page, up), f'my cluster {up} must be listed').to_be_visible()
    headers = _headers(page)
    assert headers == COLUMNS, (
        f'the clusters table must show the columns {COLUMNS}, got {headers}')

    row = _row(page, up)
    expect(row.locator('td').nth(0),
           f'{up} must be listed as RUNNING').to_have_text('RUNNING')
    expect(row.locator('td').nth(2),
           f'{up} must show its owner').to_contain_text(owner.name)
    expect(row.locator('td').nth(3),
           f'{up} must be in workspace default').to_have_text('default')
    expect(row.locator('td').nth(4),
           f'{up} must show its infra').to_have_text('Kubernetes (e2e-ctx)')
    expect(row.locator('td').nth(5),
           f'{up} must show its resources').to_have_text(
               re.compile(r'^1x\(cpus=1, mem=2'))
    expect(row.locator('td').nth(6),
           f'{up} must show when it started').to_have_text('10m ago')
    expect(
        _row(page, stopped).locator('td').nth(0),
        f'{stopped} must be listed as STOPPED').to_have_text('STOPPED')
    expect(page.get_by_text('Showing your clusters only.'),
           'My Clusters must say the table is scoped').to_be_visible()
    expect(
        page.get_by_role('link', name=theirs, exact=True),
        f"another user's cluster {theirs} must not be listed under My "
        'Clusters').to_have_count(0)


@with_twists(
    Twist('dom-no-view-all-clusters-button',
          'dom',
          expect=r'"View all clusters" must be offered',
          apply=remove_button('View all clusters')),
    Twist('res-others-count-short',
          'res',
          expect=r'the empty state must say how many clusters other users own',
          apply=_rewrite_result(
              STATUS, lambda b: _is_list(b) and b.get('all_users') is True,
              lambda v, b: v[1:])),
    Twist('srv-clusters-status-ignores-all-users',
          'srv',
          expect=r'a user with no clusters must be pointed to other users'),
    # Every user's clusters come back as "mine", so the viewer is never told
    # the table is scoped to them.
    Twist('src-clusters-status-ignores-scope',
          'src',
          expect=r'a user with no clusters must be pointed to other users',
          patch='twist_patches/clusters-status-ignores-scope.patch'),
)
@pytest.mark.exclusive
def test_zero_cluster_viewer_is_pointed_to_all_clusters(make_user, cluster,
                                                        open_as, uid, twist):
    owner = make_user('owner')
    viewer = make_user('viewer')
    cluster(f'{uid}-a', owner.user_id, status='UP')
    cluster(f'{uid}-b', owner.user_id, status='STOPPED')

    page = open_as(viewer, '/dashboard/clusters')

    expect(
        page.get_by_text("You don't have any active clusters"),
        'a user with no clusters must be pointed to other users\' clusters',
    ).to_be_visible()
    total = seed.count_clusters()
    noun = 'cluster' if total == 1 else 'clusters'
    expect(
        page.get_by_text(
            re.compile(rf'(^|\D){total:,} {noun} owned by other users')),
        f'the empty state must say how many clusters other users own ({total})',
    ).to_be_visible()
    expect(
        page.get_by_role('button', name='View all clusters'),
        '"View all clusters" must be offered to a user with no clusters',
    ).to_be_visible()


@with_twists(
    Twist('dom-view-all-clusters-does-nothing',
          'dom',
          expect=r'View all clusters must switch the URL to owner=all',
          apply=swallow_click('View all clusters')),
    Twist('res-all-clusters-drop-owner',
          'res',
          expect=r'must show its owner',
          apply=_rewrite_result(
              STATUS, lambda b: _is_list(b) and b.get('all_users') is True,
              _edit_clusters(r'^t[0-9a-f]{6}-[ab]$', user_name=None))),
    Twist('srv-clusters-status-ignores-all-users',
          'srv',
          expect=r'"View all clusters" must be offered'),
)
@pytest.mark.exclusive
def test_view_all_clusters_shows_other_users_clusters(make_user, cluster,
                                                      open_as, uid, twist):
    owner = make_user('owner')
    viewer = make_user('viewer')
    names = [
        cluster(f'{uid}-a', owner.user_id, status='UP', launched_ago_s=60),
        cluster(f'{uid}-b', owner.user_id, status='STOPPED', launched_ago_s=90),
    ]

    page = open_as(viewer, '/dashboard/clusters')
    view_all = page.get_by_role('button', name='View all clusters')
    expect(view_all,
           '"View all clusters" must be offered to a user with no clusters'
          ).to_be_visible()
    view_all.click()

    expect(page,
           'View all clusters must switch the URL to owner=all').to_have_url(
               re.compile(r'[?&]owner=all'))
    expect(
        _tab(page, 'Filter clusters by owner', 'All Clusters'),
        'View all clusters must select the All Clusters tab').to_have_attribute(
            'aria-selected', 'true')
    # The empty state already fetched every user's clusters; All Clusters may
    # reuse that result instead of asking again.
    reqs = [b for b in page.recorder.posts(STATUS) if _is_list(b)]
    assert any(b.get('all_users') is True for b in reqs), (
        f'All Clusters must ask the server for every user\'s clusters, got {reqs}'
    )
    for name in names:
        row = _row(page, name)
        expect(
            row, f"another user's cluster {name} must be listed under All "
            'Clusters').to_be_visible()
        expect(
            row.locator('td').nth(2),
            f'{name} must show its owner {owner.name}').to_contain_text(
                owner.name)
    total = seed.count_clusters()
    expect(
        page.get_by_text(
            re.compile(rf'1\s*–\s*{min(10, total)}\s+of\s+{total}\b')),
        f'All Clusters footer must read "1 – {min(10, total)} of {total}"',
    ).to_be_visible()


# --- list: history window and URL state ----------------------------------------


@with_twists(
    Twist('res-history-drops-terminated',
          'res',
          expect=r'my terminated cluster .* must be listed under All',
          apply=_rewrite_result(COST_REPORT, lambda b: True,
                                _drop_clusters(r'-hist-mine$'))),
    Twist('srv-cost-report-empty-window',
          'srv',
          expect=r'my terminated cluster .* must be listed under All'),
    Twist('dom-no-duration-column',
          'dom',
          expect=r'the All view must add a Duration column',
          apply=run_script("""
            new MutationObserver(() => document.querySelectorAll('thead th')
              .forEach(th => { if (th.innerText.startsWith('Duration')) th.remove(); }))
              .observe(document, { childList: true, subtree: true });
          """)),
    Twist('dom-all-tab-does-nothing',
          'dom',
          expect=r'the All tab must switch the URL to history=1d',
          apply=swallow_click('All')),
)
def test_all_view_adds_terminated_clusters(make_user, cluster, open_as, uid,
                                           twist):
    owner = make_user('owner')
    other = make_user('other')
    live = cluster(f'{uid}-live', owner.user_id, status='UP')
    mine = cluster(f'{uid}-hist-mine', owner.user_id, status='UP')
    seed_clusters.terminate_into_history(mine,
                                         owner.user_id,
                                         'default',
                                         started_ago_s=3 * 3600,
                                         ended_ago_s=2 * 3600)
    theirs = cluster(f'{uid}-hist-theirs', other.user_id, status='UP')
    seed_clusters.terminate_into_history(theirs,
                                         other.user_id,
                                         'default',
                                         started_ago_s=3 * 3600,
                                         ended_ago_s=2 * 3600)

    page = open_as(owner, '/dashboard/clusters')
    expect(_row(page, live),
           f'my live cluster {live} must be listed').to_be_visible()
    expect(
        page.get_by_role('link', name=mine, exact=True),
        'a terminated cluster must not be listed under Active').to_have_count(0)

    since = page.recorder.mark()
    _tab(page, 'Filter clusters by activity', 'All').click()
    expect(page, 'the All tab must switch the URL to history=1d').to_have_url(
        re.compile(r'[?&]history=1d'))
    body = _wait_post(page, COST_REPORT, since, lambda b: True, 'history')
    assert body.get('days') == 1, (
        f'the All view must ask for one day of history, got {body}')
    expect(
        page.get_by_role('combobox').filter(has_text=re.compile(r'^1 day$')),
        'the All view must show a 1 day history window').to_be_visible()
    row = _row(page, mine)
    expect(row, f'my terminated cluster {mine} must be listed under All'
          ).to_be_visible()
    headers = _headers(page)
    assert 'Duration' in headers, (
        f'the All view must add a Duration column, got {headers}')
    expect(row.locator('td').nth(0),
           f'{mine} must be listed as TERMINATED').to_have_text('TERMINATED')
    expect(
        row.locator('td').nth(headers.index('Duration')),
        f'{mine} must show how long it ran').to_have_text('1h')
    expect(_row(page, live),
           f'my live cluster {live} must stay listed').to_be_visible()
    expect(
        page.get_by_role('link', name=theirs, exact=True),
        f"another user's terminated cluster {theirs} must not be listed "
        'under My Clusters').to_have_count(0)

    _tab(page, 'Filter clusters by activity', 'Active').click()
    expect(page,
           'the Active tab must drop history from the URL').not_to_have_url(
               re.compile(r'[?&]history='))
    expect(
        page.get_by_role('link', name=mine, exact=True),
        'a terminated cluster must not be listed under Active').to_have_count(0)


@with_twists(
    Twist('dom-url-state-dropped',
          'dom',
          expect=r'a link with owner=all must open All Clusters',
          apply=_strip_url_params('owner', 'history')),
    Twist('srv-cost-report-window-ignored',
          'srv',
          expect=r'a cluster that ended 2 days ago must be listed in a 5 day '
          r'window'),
    Twist('res-history-window-select-wrong',
          'res',
          expect=r'a cluster that ended 2 days ago must be listed in a 5 day '
          r'window',
          apply=_rewrite_result(COST_REPORT, lambda b: True,
                                _drop_clusters(r'-hist-old$'))),
)
def test_shared_link_restores_scope_window_and_filter(make_user, cluster,
                                                      open_as, uid, twist):
    owner = make_user('owner')
    other = make_user('other')
    mine = cluster(f'{uid}-mine', owner.user_id, status='UP')
    theirs = cluster(f'{uid}-theirs', other.user_id, status='UP')
    old = cluster(f'{uid}-hist-old', other.user_id, status='UP')
    seed_clusters.terminate_into_history(old,
                                         other.user_id,
                                         'default',
                                         started_ago_s=3 * 86400,
                                         ended_ago_s=2 * 86400)

    page = open_as(owner,
                   f'/dashboard/clusters?owner=all&history=5d&cluster={uid}')

    expect(_tab(page, 'Filter clusters by owner', 'All Clusters'),
           'a link with owner=all must open All Clusters').to_have_attribute(
               'aria-selected', 'true')
    expect(_tab(page, 'Filter clusters by activity', 'All'),
           'a link with history=5d must open the All view').to_have_attribute(
               'aria-selected', 'true')
    expect(
        page.get_by_role('combobox').filter(has_text=re.compile(r'^5 days$')),
        'a link with history=5d must show a 5 days window').to_be_visible()
    body = _wait_post(page, COST_REPORT, 0, lambda b: True, 'history')
    assert body.get('days') == 5, (
        f'a link with history=5d must ask for five days of history, got {body}')
    expect(_chip(page, 'Cluster', uid),
           'a link with cluster= must restore the filter chip').to_be_visible()
    for name in (mine, theirs):
        expect(_row(page, name),
               f'{name} must be listed under All Clusters').to_be_visible()
    expect(
        _row(page, old),
        'a cluster that ended 2 days ago must be listed in a 5 day window',
    ).to_be_visible()
    reqs = [b for b in page.recorder.posts(STATUS) if _is_list(b)]
    assert all(b.get('all_users') is not False for b in reqs), (
        f'a link with owner=all must not ask for my clusters only, got {reqs}')
    expect(page.get_by_role('link', name=f'{uid}-zzz', exact=True),
           'nothing else may match').to_have_count(0)


# --- list: filters -------------------------------------------------------------


@with_twists(
    Twist('dom-no-labels-property',
          'dom',
          expect=r'Filter Property must offer',
          apply=remove_element('[role="option"]:last-child')),
    Twist('dom-filter-option-does-nothing',
          'dom',
          expect=r'choosing STOPPED must add a "Status : STOPPED" chip',
          apply=_swallow_clicks(_FILTER_OPTION)),
    Twist(
        'dom-chip-clear-does-nothing',
        'dom',
        expect=r'clearing the chip must bring back',
        apply=_swallow_clicks('div.rounded-full button[title="Clear filter"]')),
)
def test_status_filter_chip_and_url(make_user, cluster, open_as, uid, twist):
    owner = make_user('owner')
    up = cluster(f'{uid}-up', owner.user_id, status='UP')
    stopped = cluster(f'{uid}-stop', owner.user_id, status='STOPPED')

    page = open_as(owner, '/dashboard/clusters')
    expect(_row(page, up), f'{up} must be listed').to_be_visible()

    page.get_by_role('combobox', name='Filter Property').click()
    options = page.get_by_role('option').all_inner_texts()
    want = ['Status', 'Cluster', 'User', 'Workspace', 'Infra', 'Labels']
    assert options == want, (
        f'Filter Property must offer {want}, got {options}')
    page.keyboard.press('Escape')

    _pick_filter(page, 'Status', 'STOPPED')
    expect(
        _chip(page, 'Status', 'STOPPED'),
        'choosing STOPPED must add a "Status : STOPPED" chip').to_be_visible()
    expect(page, 'the Status filter must be kept in the URL').to_have_url(
        re.compile(r'[?&]status=STOPPED'))
    expect(_row(page, stopped),
           f'{stopped} must match Status : STOPPED').to_be_visible()
    expect(page.get_by_role('link', name=up, exact=True),
           f'{up} must be hidden by Status : STOPPED').to_have_count(0)

    _chip(page, 'Status', 'STOPPED').get_by_title('Clear filter').click()
    expect(page.get_by_role('link', name=up, exact=True),
           f'clearing the chip must bring back {up}').to_have_count(1)
    expect(page,
           'clearing the chip must drop status from the URL').not_to_have_url(
               re.compile(r'[?&]status='))


@with_twists(
    Twist('dom-refetch-per-keystroke',
          'dom',
          expect=r'must not refetch while typing',
          apply=run_script("""
            document.addEventListener('input', (e) => {
              if (e.target.placeholder === 'Filter clusters') {
                const r = document.querySelector('button[title="Refresh"]');
                if (r) r.click();
              }
            }, true);
          """)),
    Twist('dom-no-filter-box',
          'dom',
          expect=r'"Filter clusters" box must be present',
          apply=remove_element('input[placeholder="Filter clusters"]')),
)
def test_typing_in_cluster_filter_does_not_refetch(make_user, cluster, open_as,
                                                   uid, twist):
    owner = make_user('owner')
    cluster(f'{uid}-train-a', owner.user_id, status='UP')
    cluster(f'{uid}-eval-b', owner.user_id, status='UP')

    page = open_as(owner, '/dashboard/clusters')
    box = page.get_by_placeholder('Filter clusters')
    expect(box, '"Filter clusters" box must be present').to_be_visible()
    expect(_row(page, f'{uid}-train-a'),
           'seeded cluster must be listed').to_be_visible()

    since = page.recorder.mark()
    # 450 ms per key is longer than a 350 ms debounce that would refetch
    # mid-word.
    box.press_sequentially(f'{uid}-train', delay=450)
    page.wait_for_timeout(1500)

    refetches = page.recorder.posts(STATUS, since)
    assert not refetches, ('the clusters table must not refetch while typing, '
                           f'got {len(refetches)} requests')
    expect(page.get_by_text('No active clusters'),
           'the table must not flash empty while typing').to_have_count(0)
    expect(_row(page, f'{uid}-eval-b'),
           'typing without choosing a value must not filter the table'
          ).to_be_visible()


@with_twists(
    Twist('dom-workspace-option-does-nothing',
          'dom',
          expect=r'choosing workspace .* must add a Workspace chip',
          apply=_swallow_clicks(_FILTER_OPTION)),
    Twist('dom-url-workspace-dropped',
          'dom',
          expect=r'a link with workspace=.* must restore the Workspace chip',
          apply=_strip_url_params('workspace')),
    Twist('res-status-wrong-workspace',
          'res',
          expect=r'the Workspace column must show the name',
          apply=_rewrite_result(
              STATUS, _is_list,
              _edit_clusters(r'-in-ws$',
                             workspace='default',
                             workspace_name='default'))),
)
@pytest.mark.exclusive
def test_workspace_filter_and_shared_link(make_user, cluster, workspace,
                                          open_as, uid, twist):
    owner = make_user('owner')
    ws_name = f'{uid}-team'
    ws_id = workspace(ws_name)
    in_default = cluster(f'{uid}-in-default', owner.user_id, status='UP')
    in_ws = cluster(f'{uid}-in-ws', owner.user_id, status='UP', workspace=ws_id)

    page = open_as(owner, '/dashboard/clusters')
    expect(
        _row(page, in_ws).locator('td').nth(3),
        f'the Workspace column must show the name {ws_name}').to_have_text(
            ws_name)

    _pick_filter(page, 'Workspace', ws_name)
    expect(_chip(page, 'Workspace', ws_name),
           f'choosing workspace {ws_name} must add a Workspace chip'
          ).to_be_visible()
    expect(page, 'the Workspace filter must be kept in the URL').to_have_url(
        re.compile(rf'[?&]workspace={re.escape(ws_name)}'))
    expect(
        _row(page, in_ws),
        f'cluster {in_ws} in workspace {ws_name} must match its Workspace '
        'filter').to_be_visible()
    expect(
        page.get_by_role('link', name=in_default, exact=True),
        f'{in_default} must be hidden by the Workspace filter').to_have_count(0)

    page.reload()
    expect(_chip(page, 'Workspace', ws_name),
           f'a link with workspace={ws_name} must restore the Workspace chip'
          ).to_be_visible()
    expect(
        _row(page, in_ws),
        f'cluster {in_ws} in workspace {ws_name} must match its Workspace '
        'filter after a reload').to_be_visible()
    expect(page.get_by_role('link', name=in_default, exact=True),
           f'{in_default} must stay hidden after a reload').to_have_count(0)


# --- list: row actions ---------------------------------------------------------


@with_twists(
    Twist('res-status-reports-stopped-actions',
          'res',
          expect=r'a RUNNING cluster must offer Connect and VSCode',
          apply=_rewrite_result(STATUS, _is_list,
                                _edit_clusters(r'-up$', status='STOPPED'))),
    Twist('dom-connect-does-nothing',
          'dom',
          expect=r'Connect must open the SSH instructions',
          apply=_swallow_clicks('td:last-child button:first-of-type')),
    Twist('dom-vscode-does-nothing',
          'dom',
          expect=r'VSCode must open the VS Code instructions',
          apply=_swallow_clicks('td:last-child button:nth-of-type(2)')),
)
def test_connect_and_vscode_open_instructions(make_user, cluster, open_as, uid,
                                              twist):
    owner = make_user('owner')
    up = cluster(f'{uid}-up', owner.user_id, status='UP')
    stopped = cluster(f'{uid}-stop', owner.user_id, status='STOPPED')

    page = open_as(owner, '/dashboard/clusters')
    expect(_row(page, up), f'{up} must be listed').to_be_visible()
    # The buttons carry no accessible name, only a hover tooltip.
    actions = _row(page, up).locator('td').last.locator('button')
    expect(actions,
           'a RUNNING cluster must offer Connect and VSCode').to_have_count(2)
    expect(
        _row(page, stopped).locator('td').last.locator('button'),
        'a STOPPED cluster must not offer Connect or VSCode').to_have_count(0)

    actions.nth(0).click()
    dialog = page.get_by_role('dialog')
    expect(dialog, 'Connect must open the SSH instructions').to_contain_text(
        f'Connect to: {up}')
    expect(dialog.locator('pre code'),
           'the SSH instructions must give the commands').to_have_text(
               [f'sky status {up}', f'ssh {up}'])
    page.keyboard.press('Escape')
    expect(dialog, 'the SSH instructions must close').to_have_count(0)

    actions.nth(1).click()
    expect(dialog, 'VSCode must open the VS Code instructions').to_contain_text(
        f'code --remote ssh-remote+{up} "/home"')


@with_twists(
    Twist('res-workspaces-all-writable',
          'res',
          expect=r'Connect must be disabled for a non-member',
          apply=_rewrite_result('/workspaces', lambda b: True,
                                lambda value, body: _all_writable(value))),
    Twist('res-status-drops-workspace',
          'res',
          expect=r'Connect must be disabled for a non-member',
          apply=_rewrite_result(STATUS, lambda b: 'summary_response' in b,
                                _edit_clusters(r'-ro$', workspace=None))),
)
@pytest.mark.exclusive
def test_read_only_workspace_disables_connect(make_user, cluster, workspace,
                                              open_as, uid, twist):
    owner = make_user('owner')
    viewer = make_user('viewer')
    ws_id = workspace(f'{uid}-shared',
                      private=True,
                      allowed_users=[owner.user_id],
                      read_access='all')
    ro = cluster(f'{uid}-ro', owner.user_id, status='UP', workspace=ws_id)

    page = open_as(viewer, f'/dashboard/clusters?owner=all&cluster={uid}')
    row = _row(page, ro)
    expect(row, f'{ro} in a read-only workspace must be visible to everyone'
          ).to_be_visible()
    expect(
        row.locator('td').last.locator('button'),
        'Connect must be disabled for a non-member of the workspace'
    ).to_have_count(0)
    disabled = row.locator('td').last.locator('span[title="connect"]')
    expect(disabled, 'Connect must be shown greyed out').to_be_visible()
    _hover(page, disabled)
    expect(
        page.get_by_text(
            re.compile(r'Read-only: you are not a member of this cluster')),
        'the greyed out action must explain why').to_be_visible()

    detail = open_as(viewer, f'/dashboard/clusters/{ro}')
    expect(detail.get_by_text('Details', exact=True),
           'the detail page must load').to_be_visible()
    expect(
        detail.get_by_role('button', name='Connect'),
        'Connect must be disabled for a non-member on the detail page too',
    ).to_have_count(0)


def _all_writable(value: Dict[str, Any]) -> Dict[str, Any]:
    """Mark every workspace writable, as a server that ignored membership
    would."""
    for cfg in value.values():
        if isinstance(cfg, dict):
            cfg['writable'] = True
    return value


def _hover(page: Page, locator) -> None:
    """Hover that opens the dashboard's tooltips. They need real pointer
    movement: Playwright's one-step ``hover()`` never opens them."""
    box = locator.bounding_box()
    assert box, 'the element to hover must be on screen'
    page.mouse.move(max(box['x'] - 40, 0), max(box['y'] - 40, 0))
    page.mouse.move(box['x'] + box['width'] / 2,
                    box['y'] + box['height'] / 2,
                    steps=8)


@with_twists(
    Twist('res-autostop-dropped',
          'res',
          expect=r"autostop must read '90m \(down\)'",
          apply=_rewrite_result(STATUS, _is_list,
                                _edit_clusters(r'-down$', autostop=-1))),
    Twist('res-autodown-dropped',
          'res',
          expect=r"autostop must read '90m \(down\)'",
          apply=_rewrite_result(STATUS, _is_list,
                                _edit_clusters(r'-down$', to_down=False))),
)
def test_autostop_column(make_user, cluster, open_as, uid, twist):
    owner = make_user('owner')
    down = cluster(f'{uid}-down', owner.user_id, status='UP')
    seed_clusters.set_details(down, autostop=90, to_down=True)
    stop = cluster(f'{uid}-stop2h', owner.user_id, status='UP')
    seed_clusters.set_details(stop, autostop=120)
    none = cluster(f'{uid}-none', owner.user_id, status='UP')

    page = open_as(owner, '/dashboard/clusters')
    col = COLUMNS.index('Autostop')
    expect(
        _row(page, down).locator('td').nth(col),
        "autostop must read '90m (down)' for autodown after 90 minutes"
    ).to_have_text('90m (down)')
    expect(
        _row(page, stop).locator('td').nth(col),
        "autostop must read '2h' for autostop after 120 minutes").to_have_text(
            '2h')
    expect(
        _row(page, none).locator('td').nth(col),
        "autostop must read '-' when autostop is off").to_have_text('-')


# --- detail --------------------------------------------------------------------

_YAML = 'name: e2e-task\nresources:\n  cpus: 1\nrun: echo e2e\n'


def _detail_field(page: Page, label: str):
    return page.locator('div.text-gray-600', has_text=re.compile(
        rf'^{label}$')).locator('xpath=following-sibling::div[1]')


@with_twists(
    Twist('srv-cluster-detail-summary-only',
          'srv',
          expect=r'Last Event must show the latest status event'),
    Twist('src-clusters-detail-drops-last-event',
          'src',
          expect=r'Last Event must show the latest status event',
          patch='twist_patches/clusters-detail-drops-last-event.patch'),
    Twist('res-detail-autostop-dropped',
          'res',
          expect=r"Autostop must read '90m \(down\)'",
          apply=_rewrite_result(STATUS, lambda b: bool(b.get('cluster_names')),
                                _edit_clusters(r'-box$', autostop=-1))),
    Twist('req-queue-wrong-cluster',
          'req',
          expect=r'the cluster jobs table must query this cluster',
          apply=rewrite_request(
              QUEUE, lambda b: {
                  **b, 'cluster_name': 'not-this-cluster'
              })),
)
def test_cluster_detail_shows_details(make_user, cluster, open_as, uid, twist):
    owner = make_user('owner')
    name = cluster(f'{uid}-box', owner.user_id, status='UP', launched_ago_s=300)
    command = f'sky launch -c {name} task.yaml'
    seed_clusters.set_details(name,
                              autostop=90,
                              to_down=True,
                              command=command,
                              task_yaml=_YAML)
    event = seed_clusters.add_event(name, 'cluster is up and healthy')

    page = open_as(owner, f'/dashboard/clusters/{name}')

    body = _wait_post(page, STATUS, 0, lambda b: b.get('cluster_names'),
                      'cluster lookup')
    assert body.get('cluster_names') == [
        name
    ] and body.get('summary_response') is False, (
        f'the detail page must look up {name} in full, got {body}')
    expect(page.get_by_text('Details', exact=True),
           f'the detail page must show the Details card for {name}'
          ).to_be_visible()
    expect(_detail_field(page, 'Last Event'),
           'Last Event must show the latest status event').to_have_text(event)
    expect(_detail_field(page, 'Status'),
           'Status must read RUNNING').to_have_text('RUNNING')
    expect(_detail_field(page, 'Cluster'),
           f'Cluster must read {name}').to_have_text(name)
    expect(_detail_field(page, 'User'),
           f'User must show {owner.name}').to_contain_text(owner.name)
    expect(_detail_field(page, 'Infra'),
           'Infra must show the context').to_have_text('Kubernetes (e2e-ctx)')
    expect(_detail_field(page, 'Resources'),
           'Resources must show the full resources').to_have_text(
               '1x(cpus=1, mem=2, disk=256)')
    launched = time.strftime('%Y-%m-%d', time.localtime(time.time() - 300))
    expect(_detail_field(page, 'Started'),
           'Started must show the launch time').to_contain_text(launched)
    expect(_detail_field(page, 'Autostop'),
           "Autostop must read '90m (down)'").to_have_text('90m (down)')
    expect(
        page.locator('code').filter(has_text=command),
        'Entrypoint must show the launch command').to_be_visible()
    queue = _wait_post(page, QUEUE, 0, lambda b: True, 'cluster jobs')
    assert queue.get('cluster_name') == name and queue.get(
        'override_skypilot_config', {}).get('active_workspace') == 'default', (
            'the cluster jobs table must query this cluster in its workspace, '
            f'got {queue}')
    expect(page.get_by_text('Cluster Jobs', exact=True),
           'the Cluster Jobs table must be shown').to_be_visible()
    expect(page.get_by_text('Provision Logs', exact=True),
           'the Provision Logs section must be shown').to_be_visible()
    expect(page.get_by_role('button', name='Connect'),
           'a RUNNING cluster must offer Connect on its detail page'
          ).to_be_visible()


@with_twists(
    Twist('dom-show-yaml-does-nothing',
          'dom',
          expect=r'Show SkyPilot YAML must expand the launch YAML',
          apply=swallow_click('Show SkyPilot YAML')),
    Twist('res-detail-yaml-dropped',
          'res',
          expect=r'Show SkyPilot YAML must be offered',
          apply=_rewrite_result(
              STATUS, lambda b: bool(b.get('cluster_names')),
              _edit_clusters(r'-yaml$', last_creation_yaml=None))),
    Twist('dom-copy-yaml-does-nothing',
          'dom',
          expect=r'Copy YAML must copy the launch YAML',
          apply=_swallow_clicks('button[title="Copy YAML"]')),
)
def test_cluster_detail_yaml_and_copy(make_user, cluster, open_as, uid, twist,
                                      e2e_base_url):
    owner = make_user('owner')
    name = cluster(f'{uid}-yaml', owner.user_id, status='UP')
    command = f'sky launch -c {name} task.yaml'
    # Unique per test: the clipboard is shared by every context of a
    # browser, so another test's copy must not satisfy this one.
    run_line = f'run: echo {uid}'
    seed_clusters.set_details(name,
                              command=command,
                              task_yaml=_YAML.replace('run: echo e2e',
                                                      run_line))

    page = open_as(owner, f'/dashboard/clusters/{name}')
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'],
                                   origin=e2e_base_url)
    toggle = page.get_by_role('button', name='Show SkyPilot YAML')
    expect(toggle, 'Show SkyPilot YAML must be offered').to_be_visible()
    toggle.click()
    expect(page.get_by_text(run_line),
           'Show SkyPilot YAML must expand the launch YAML').to_be_visible()

    page.evaluate("navigator.clipboard.writeText('')")
    page.locator('button[title="Copy YAML"]').click()
    copied = _read_clipboard(page)
    assert run_line in copied, (
        f'Copy YAML must copy the launch YAML, got {copied!r}')
    page.locator('button[title="Copy command"]').click()
    deadline = time.time() + 5
    while time.time() < deadline and _read_clipboard(page) != command:
        page.wait_for_timeout(200)
    assert _read_clipboard(page) == command, (
        'Copy command must copy the launch command')


def _read_clipboard(page: Page, timeout_s: float = 5) -> str:
    deadline = time.time() + timeout_s
    text = ''
    while time.time() < deadline:
        text = page.evaluate('navigator.clipboard.readText()')
        if text:
            return text
        page.wait_for_timeout(200)
    return text


@with_twists(
    Twist('dom-refresh-jobs-does-nothing',
          'dom',
          expect=r'Refresh Jobs must query the cluster job queue again',
          apply=swallow_click('Refresh Jobs')),
    Twist('dom-no-refresh-jobs',
          'dom',
          expect=r'Refresh Jobs must be offered',
          apply=remove_button('Refresh Jobs')),
)
def test_refresh_jobs_requeries_queue(make_user, cluster, open_as, uid, twist):
    owner = make_user('owner')
    name = cluster(f'{uid}-jobs', owner.user_id, status='UP')

    page = open_as(owner, f'/dashboard/clusters/{name}')
    _wait_post(page, QUEUE, 0, lambda b: True, 'cluster jobs')
    expect(page.get_by_text('No jobs found'),
           'a cluster with no jobs must say so').to_be_visible()
    refresh = page.get_by_role('button', name='Refresh Jobs')
    expect(refresh, 'Refresh Jobs must be offered').to_be_enabled()
    since = page.recorder.mark()
    refresh.click()
    deadline = time.time() + 15
    while time.time() < deadline and not page.recorder.posts(QUEUE, since):
        page.wait_for_timeout(200)
    reqs = page.recorder.posts(QUEUE, since)
    assert reqs and reqs[-1].get('cluster_name') == name, (
        f'Refresh Jobs must query the cluster job queue again, got {reqs}')
