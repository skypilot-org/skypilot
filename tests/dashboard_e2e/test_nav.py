"""Navigation and page-level behaviour shared by every dashboard page.

Every top-level page must render its title, header and primary controls
without failed requests; the top bar must route to each page and highlight
the one that is open; the first-visit tour must stay away once it has been
seen; Refresh must reload a page's data; and a page load must not ask for the
caller's role over and over.

These tests only read pages and assert on rows they create, so they run in
parallel with every other test.
"""
import json
import re
import time
from typing import Any, Callable, Dict, List

from framework import seed
from framework.twists import remove_element
from framework.twists import rewrite_response
from framework.twists import run_script
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import BrowserContext
from playwright.sync_api import expect
from playwright.sync_api import Page
from playwright.sync_api import Response
from playwright.sync_api import Route
import seed_recipes

USERS_ROLE = '/users/role'
RECIPES_LIST = '/recipes/list'

# Each OSS top-level page: where it lands, its <title>, and a partial ARIA
# template of its main region (header, filters, primary buttons, table head).
# Templates match in order and ignore anything they do not name.
PAGES: List[Dict[str, str]] = [
    {
        'path': '/dashboard/',
        'url': '/dashboard/clusters',
        'title': 'Clusters | SkyPilot Dashboard',
        'aria': """
- link "Sky Clusters"
- textbox "Filter clusters"
- tablist "Filter clusters by owner":
  - tab "My Clusters"
  - tab "All Clusters"
- button "Refresh"
- table:
  - rowgroup:
    - row /Status Cluster User Workspace Infra Resources/
""",
    },
    {
        'path': '/dashboard/jobs',
        'url': '/dashboard/jobs',
        'title': 'Managed Jobs | SkyPilot Dashboard',
        'aria': """
- link "Managed Jobs"
- textbox "Filter jobs"
- tablist "Filter jobs by owner":
  - tab "My Jobs"
  - tab "All Jobs"
- button "Refresh"
- table:
  - rowgroup:
    - row /ID.* Name User Submitted Duration Status/
""",
    },
    {
        'path': '/dashboard/volumes',
        'url': '/dashboard/volumes',
        'title': 'Volumes | SkyPilot Dashboard',
        'aria': """
- link "Volumes"
- button "Refresh"
- textbox "Filter volumes"
- table:
  - rowgroup:
    - row /Name Infra Status Size User/
""",
    },
    {
        'path': '/dashboard/recipes',
        'url': '/dashboard/recipes',
        'title': 'Recipes | SkyPilot Dashboard',
        'aria': """
- button "Refresh"
- button "New Recipe"
- heading "Pinned Recipes" [level=2]
- heading "My Recipes" [level=2]
- textbox "Filter recipes"
- table:
  - rowgroup:
    - row /Type Name Description Owner/
""",
    },
    {
        'path': '/dashboard/infra',
        'url': '/dashboard/infra',
        'title': 'Infra | SkyPilot Dashboard',
        'aria': """
- link "Infrastructure"
- combobox
- button "Refresh"
""",
    },
    {
        'path': '/dashboard/workspaces',
        'url': '/dashboard/workspaces',
        'title': 'Workspaces | SkyPilot Dashboard',
        'aria': """
- button "Refresh"
- textbox "Filter workspaces"
- button "Create Workspace"
- table:
  - rowgroup:
    - row /Workspace.* Running Clusters Jobs Enabled infra/
""",
    },
    {
        'path': '/dashboard/users',
        'url': '/dashboard/users',
        'title': 'Users | SkyPilot Dashboard',
        'aria': """
- button "Users"
- button "Service Accounts"
- button "+ New User"
- button "Refresh"
- textbox "Filter users"
- table:
  - rowgroup:
    - row /Name User ID Role Type Joined/
""",
    },
    {
        'path': '/dashboard/settings',
        'url': '/dashboard/settings/config',
        'title': 'Settings | SkyPilot Dashboard',
        'aria': """
- heading "Edit SkyPilot API Server Configuration" [level=3]
- textbox
- button "Apply"
""",
    },
]

# Feature probes that answer 404 when the feature is absent: the settings page
# asks whether Grafana is deployed.
EXPECTED_FAILURES = {'/grafana/api/health'}

# Measured GET /users/role calls per page load (admin, settled page). A page
# that calls it more often has grown another uncached role lookup.
ROLE_CALLS_MAX = {
    '/dashboard/clusters': 2,
    '/dashboard/jobs': 2,
    '/dashboard/volumes': 2,
    '/dashboard/recipes': 3,
    '/dashboard/infra': 2,
    '/dashboard/workspaces': 3,
    '/dashboard/users': 3,
    '/dashboard/settings/config': 1,
}

# Top bar links in order, and where each must land.
TOP_LINKS = [
    ('Jobs', '/dashboard/jobs'),
    ('Volumes', '/dashboard/volumes'),
    ('Recipes', '/dashboard/recipes'),
    ('Infra', '/dashboard/infra'),
    ('Workspaces', '/dashboard/workspaces'),
    ('Users', '/dashboard/users'),
    ('Clusters', '/dashboard/clusters'),
]

ACTIVE_LINKS_JS = """
  () => [...document.querySelectorAll('a.border-b-2')]
    .filter(a => a.classList.contains('text-blue-600'))
    .map(a => a.getAttribute('href'))
"""

CLEAR_TOUR_ONCE = """
  try {
    if (!sessionStorage.getItem('e2e-tour-cleared')) {
      localStorage.removeItem('skypilot-dashboard-first-visit');
      localStorage.removeItem('skypilot-dashboard-tour-completed');
      sessionStorage.setItem('e2e-tour-cleared', '1');
    }
  } catch (e) {}
"""

WATCH_REFRESH_DISABLED = """
  () => {
    window.__e2eRefreshDisabled = false;
    const seen = () => [...document.querySelectorAll('button')]
      .filter(b => b.innerText.trim() === 'Refresh')
      .some(b => b.disabled);
    new MutationObserver(() => {
      if (seen()) window.__e2eRefreshDisabled = true;
    }).observe(document, {subtree: true, childList: true, attributes: true,
                          attributeFilter: ['disabled']});
  }
"""

# --- helpers -------------------------------------------------------------------


def _path(url: str) -> str:
    return '/' + url.split('://', 1)[-1].split('/', 1)[-1].split('?')[0]


def _role_calls(page: Page, since: int = 0) -> int:
    return sum(1 for r in page.recorder.requests[since:]
               if r.method == 'GET' and _path(r.url).endswith(USERS_ROLE))


def _settle(page: Page) -> None:
    """Wait until the page stops sending requests."""
    try:
        page.wait_for_load_state('networkidle', timeout=30_000)
    except Exception:  # pylint: disable=broad-except
        pass


def _wait_post(page: Page,
               path: str,
               since: int,
               message: str,
               pred: Callable[[Dict[str, Any]], bool] = lambda b: True,
               timeout_s: float = 15) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for body in page.recorder.posts(path, since):
            if pred(body):
                return body
        page.wait_for_timeout(200)
    raise AssertionError(f'{message} (no matching POST {path})')


def _top_link(page: Page, label: str):
    """A link in the top bar (not the page header), by its visible label."""
    return page.locator('a.border-b-2').filter(
        has_text=re.compile(rf'^\s*{re.escape(label)}\s*$'))


def _rewrite_later_results(path: str, fn: Callable[[Any], Any]):
    """res twist: rewrite the async result of every POST to ``*path`` after
    the first one (what a refetch receives)."""

    def apply(context: BrowserContext) -> None:
        ids: List[str] = []

        def on_post(route: Route) -> None:
            resp = route.fetch()
            ids.append(resp.headers.get('x-skypilot-request-id'))
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            resp = route.fetch()
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in ids[1:] or resp.status != 200:
                route.fulfill(response=resp)
                return
            data = resp.json()
            if data.get('return_value'):
                data['return_value'] = json.dumps(
                    fn(json.loads(data['return_value'])))
            route.fulfill(response=resp, json=data)

        context.route(f'**{path}', on_post)
        context.route('**/api/get?*', on_get)

    return apply


def _drop_late_recipes(recipes):
    return [
        r for r in recipes if not (
            seed.is_test_name(r.get('name')) and r['name'].endswith('-late'))
    ]


def _as_role(role: str):
    return rewrite_response(USERS_ROLE, lambda d, _: {**d, 'role': role})


# --- X-01: every top-level page renders ----------------------------------------


@with_twists(
    # The server rejects the hub's list request (schema drift between the
    # dashboard and the server).
    Twist(
        'srv-nav-recipes-list-rejected',
        'srv',
        # The hub then shows an error instead of its sections.
        expect=r'/dashboard/recipes must (load without failed requests'
        r'|render its header and primary controls)'),
    Twist('dom-nav-workspaces-no-filter',
          'dom',
          expect=r'/dashboard/workspaces must render its header and primary '
          r'controls',
          apply=remove_element('input[placeholder="Filter workspaces"]')),
    Twist(
        'res-nav-admin-reported-as-viewer',
        'res',
        # Users drops "+ New User" and Settings drops the editor for a
        # viewer; whichever page comes first trips.
        expect=r'/dashboard/(users|settings) must render its header and '
        r'primary controls',
        apply=_as_role('viewer')),
)
def test_every_top_level_page_renders(make_user, open_as, twist):
    admin = make_user('admin', role='admin')
    page = None
    for spec in PAGES:
        path = spec['path']
        failed: List[str] = []
        errors: List[str] = []
        if page is None:
            page = open_as(admin, '/dashboard/clusters')

        def on_response(resp: Response, out=failed) -> None:
            if resp.status >= 400 and (_path(resp.url)
                                       not in EXPECTED_FAILURES):
                out.append(f'{resp.request.method} {_path(resp.url)} '
                           f'-> {resp.status}')

        def on_console(msg, out=errors) -> None:
            # Failed loads are checked, with their URL, by on_response.
            if msg.type == 'error' and not msg.text.startswith(
                    'Failed to load resource'):
                out.append(msg.text)

        page.on('response', on_response)
        page.on('console', on_console)
        page.goto(path)
        expect(page, f'{path} must land on {spec["url"]}').to_have_url(
            re.compile(rf'{re.escape(spec["url"])}(\?.*)?$'))
        expect(page, f'{path} must be titled "{spec["title"]}"').to_have_title(
            spec['title'])
        expect(page.get_by_role('main'),
               f'{path} must render its header and primary controls'
              ).to_match_aria_snapshot(spec['aria'])
        _settle(page)
        page.remove_listener('response', on_response)
        page.remove_listener('console', on_console)
        assert not failed, f'{path} must load without failed requests: {failed}'
        assert not errors, (f'{path} must load without console errors: '
                            f'{errors[:5]}')


# --- X-02: the top bar routes and highlights -----------------------------------


@with_twists(
    Twist('dom-nav-infra-link-dead',
          'dom',
          expect=r'the Infra link must route to /dashboard/infra',
          apply=run_script("""
            document.addEventListener('click', (e) => {
              const a = e.target.closest('a.border-b-2');
              if (a && a.innerText.trim() === 'Infra') {
                e.stopImmediatePropagation();
                e.preventDefault();
              }
            }, true);
          """)),
    Twist('dom-nav-no-active-highlight',
          'dom',
          expect=r'the top bar must highlight only',
          apply=run_script("""
            new MutationObserver(() => document
              .querySelectorAll('a.border-b-2.text-blue-600')
              .forEach(a => a.classList.remove('text-blue-600')))
              .observe(document, {subtree: true, childList: true,
                                  attributes: true,
                                  attributeFilter: ['class']});
          """)),
    Twist('src-recipes_settings_nav-users-link-to-workspaces',
          'src',
          expect=r'the Users link must point to /dashboard/users',
          patch='twist_patches/recipes_settings_nav-users-link-to-workspaces'
          '.patch'),
)
def test_top_bar_links_route_and_highlight(make_user, open_as, twist):
    admin = make_user('admin', role='admin')
    page = open_as(admin, '/dashboard/clusters')
    assert page.evaluate(ACTIVE_LINKS_JS) == [
        '/dashboard/clusters'
    ], ('the top bar must highlight only Clusters on /dashboard/clusters, got '
        f'{page.evaluate(ACTIVE_LINKS_JS)}')

    for label, href in TOP_LINKS:
        link = _top_link(page, label)
        expect(link, f'the top bar must offer {label}').to_have_count(1)
        expect(link,
               f'the {label} link must point to {href}').to_have_attribute(
                   'href', href)
        link.click()
        expect(page, f'the {label} link must route to {href}').to_have_url(
            re.compile(rf'{re.escape(href)}(\?.*)?$'))
        expect(page, f'the {label} page must load').to_have_title(
            re.compile(r'\| SkyPilot Dashboard$'))
        deadline = time.time() + 10
        active = page.evaluate(ACTIVE_LINKS_JS)
        while active != [href] and time.time() < deadline:
            page.wait_for_timeout(200)
            active = page.evaluate(ACTIVE_LINKS_JS)
        assert active == [
            href
        ], (f'the top bar must highlight only {label} on {href}, got {active}')

    config = page.locator('a[href="/dashboard/settings"]')
    expect(config, 'an admin must see the Configuration link').to_have_count(1)
    config.click()
    expect(page, 'the Configuration link must route to /dashboard/settings/'
           'config').to_have_url(re.compile(r'/dashboard/settings/config$'))
    expect(
        config,
        'the Configuration link must be highlighted on Settings').to_have_class(
            re.compile(r'(^|\s)text-blue-600(\s|$)'))
    assert page.evaluate(ACTIVE_LINKS_JS) == [], (
        'the top bar must highlight only Configuration on Settings, got '
        f'{page.evaluate(ACTIVE_LINKS_JS)}')


# --- X-09: the first-visit tour ------------------------------------------------


@with_twists(
    Twist('dom-tour-ignores-completed-flag',
          'dom',
          expect=r'the tour must not start once it has been seen',
          apply=run_script("""
            try {
              localStorage.removeItem('skypilot-dashboard-first-visit');
              localStorage.removeItem('skypilot-dashboard-tour-completed');
            } catch (e) {}
          """)),
    Twist('dom-tour-never-shown',
          'dom',
          expect=r'a first visit must start the tour',
          apply=remove_element('.shepherd-element')),
    Twist('dom-tour-skip-does-nothing',
          'dom',
          expect=r'Skip Tour must close the tour',
          apply=swallow_click('Skip Tour')),
)
def test_tour_runs_once_then_stays_away(make_user, open_as, twist):
    user = make_user('user')
    tour = None

    # A returning visitor: the suite's browser has seen the tour already.
    page = open_as(user, '/dashboard/clusters')
    tour = page.locator('.shepherd-element')
    expect(page.get_by_role('link', name='Sky Clusters'),
           'the clusters page must load').to_be_visible()
    # Absence check: the tour starts on mount and the welcome prompt after
    # 2 s, so give both that long to (wrongly) appear.
    page.wait_for_timeout(3000)
    expect(tour,
           'the tour must not start once it has been seen').to_have_count(0)
    expect(page.get_by_text('Welcome to SkyPilot!'),
           'the tour must not start once it has been seen').to_have_count(0)

    # A first visit: forget the flags once for this tab, then reload.
    page.context.add_init_script(CLEAR_TOUR_ONCE)
    page.reload()
    expect(tour.get_by_text('Welcome to SkyPilot!'),
           'a first visit must start the tour').to_be_visible()
    stored = page.evaluate(
        "localStorage.getItem('skypilot-dashboard-first-visit')")
    assert stored == 'true', (
        f'a first visit must be remembered in localStorage, got {stored!r}')
    tour.get_by_role('button', name='Skip Tour').click()
    expect(tour, 'Skip Tour must close the tour').to_have_count(0)

    page.reload()
    expect(page.get_by_role('link', name='Sky Clusters'),
           'the clusters page must load again').to_be_visible()
    page.wait_for_timeout(3000)
    expect(tour,
           'the tour must not start once it has been seen').to_have_count(0)


# --- X-10: Refresh -------------------------------------------------------------


@with_twists(
    Twist('dom-refresh-does-nothing',
          'dom',
          expect=r'Refresh must refetch the recipes',
          apply=swallow_click('Refresh')),
    Twist('dom-refresh-never-disabled',
          'dom',
          expect=r'Refresh must be disabled while the recipes reload',
          apply=run_script("""
            new MutationObserver(() => document.querySelectorAll('button')
              .forEach(b => {
                if (b.innerText.trim() === 'Refresh' && b.disabled) {
                  b.disabled = false;
                }
              }))
              .observe(document, {subtree: true, childList: true,
                                  attributes: true,
                                  attributeFilter: ['disabled']});
          """)),
    Twist('res-refresh-returns-stale-list',
          'res',
          expect=r'Refresh must show the recipe added since the page loaded',
          apply=_rewrite_later_results(RECIPES_LIST, _drop_late_recipes)),
)
def test_refresh_reloads_page_data(make_user, open_as, seed_cluster, uid,
                                   twist):
    user = make_user('user')
    late_recipe = f'{uid}-late'
    late_cluster = f'{uid}-late'
    try:
        page = open_as(user, '/dashboard/recipes')
        refresh = page.get_by_role('button', name='Refresh')
        expect(refresh, 'the hub must offer Refresh').to_be_enabled()
        expect(page.get_by_text('Updated just now'),
               'the hub must say when it last loaded').to_be_visible()
        seed_recipes.recipe(late_recipe, user.user_id, user.name)
        expect(page.get_by_role('link', name=late_recipe, exact=True),
               'a recipe added after load must not appear before Refresh'
              ).to_have_count(0)

        page.evaluate(WATCH_REFRESH_DISABLED)
        since = page.recorder.mark()
        refresh.click()
        _wait_post(page, RECIPES_LIST, since,
                   'Refresh must refetch the recipes')
        expect(refresh,
               'Refresh must be enabled again after reloading').to_be_enabled()
        assert page.evaluate('window.__e2eRefreshDisabled'), (
            'Refresh must be disabled while the recipes reload')
        expect(page.get_by_role('link', name=late_recipe, exact=True),
               'Refresh must show the recipe added since the page loaded'
              ).to_have_count(1)
        expect(page.get_by_text('Updated just now'),
               'the hub must say it was just updated').to_be_visible()

        # The clusters page: same contract, different data path.
        page.goto('/dashboard/clusters')
        refresh = page.get_by_role('button', name='Refresh')
        expect(refresh, 'the clusters page must offer Refresh').to_be_enabled()
        seed_cluster(late_cluster, user.user_id)
        since = page.recorder.mark()
        refresh.click()
        _wait_post(page, '/status', since, 'Refresh must refetch the clusters')
        expect(page.get_by_role('link', name=late_cluster, exact=True),
               'Refresh must show the cluster added since the page loaded'
              ).to_be_visible()
        expect(refresh,
               'Refresh must be enabled again after reloading').to_be_enabled()
    finally:
        seed_recipes.delete(late_recipe)


# --- X-11: GET /users/role per page load ---------------------------------------


@with_twists(
    Twist('dom-role-fetched-per-render',
          'dom',
          expect=r'must call GET /users/role at most',
          apply=run_script("""
            window.addEventListener('load', () => {
              for (let i = 0; i < 3; i++) fetch('/users/role');
            });
          """)),
    Twist('dom-role-fetch-doubled',
          'dom',
          expect=r'must call GET /users/role at most',
          apply=run_script("""
            const orig = window.fetch;
            window.fetch = function (input, init) {
              const url = typeof input === 'string' ? input : input.url;
              if (url && url.split('?')[0].endsWith('/users/role')) {
                orig.call(this, input, init);
              }
              return orig.call(this, input, init);
            };
          """)),
)
def test_page_load_role_lookups_are_bounded(make_user, open_as, twist):
    admin = make_user('admin', role='admin')
    page = open_as(admin, '/dashboard/clusters')
    counts = {}
    for path, bound in ROLE_CALLS_MAX.items():
        since = page.recorder.mark()
        page.goto(path)
        expect(page, f'{path} must load').to_have_title(
            re.compile(r'\| SkyPilot Dashboard$'))
        _settle(page)
        counts[path] = _role_calls(page, since)
        assert counts[path] >= 1, (
            f'{path} must resolve the caller role with GET /users/role')
        assert counts[path] <= bound, (
            f'{path} must call GET /users/role at most {bound} times per '
            f'load, got {counts[path]} (all pages: {counts})')
