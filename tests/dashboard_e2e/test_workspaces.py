"""Workspaces: list, create wizard, detail page, delete, private visibility.

A workspace's key in the server config is a generated id (``ws-<hex>``) and
its user-facing name is a separate field: URLs and API calls use the id, the
page shows the name. Counts on the list come from the all-users cluster and
managed-job summaries, and link into the Clusters and Jobs pages filtered by
the workspace name. Only admins may create, rename or delete workspaces; a
private workspace is visible only to its allowed users and admins.

Every test creates its own ``<uid>-...`` workspaces through the API (or the
wizard) and deletes them afterwards. Tests asserting the whole workspace list
are ``exclusive``.
"""
import dataclasses
import json
import os
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import parse_qs
from urllib.parse import urlparse

from framework.twists import remove_element
from framework.twists import rewrite_request
from framework.twists import rewrite_response
from framework.twists import run_script
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import BrowserContext
from playwright.sync_api import expect
from playwright.sync_api import Page
from playwright.sync_api import Route
import pytest
import seed_workspaces

JOBS_QUEUE = '/jobs/queue/v2'


@dataclasses.dataclass
class Workspace:
    id: str
    name: str


@pytest.fixture
def make_workspace(admin_api, uid) -> Iterator[Callable[..., Workspace]]:
    """Create ``<uid>-<label>`` workspaces through the API; every workspace
    created or ``track``-ed is deleted afterwards, with the test rows seeded
    into it."""
    names: List[str] = []

    def make(label: str, **config: Any) -> Workspace:
        name = f'{uid}-{label}'
        names.append(name)
        wid = seed_workspaces.create_workspace(admin_api, name, config)
        return Workspace(wid, name)

    def track(name: str) -> None:
        names.append(name)

    make.track = track  # type: ignore[attr-defined]
    yield make
    for name in names:
        wid = seed_workspaces.workspace_id(name)
        if wid is not None:
            ok, err = seed_workspaces.delete_workspace(admin_api, wid)
            assert ok, f'cleanup: deleting workspace {name} ({wid}): {err}'


# --- page helpers ------------------------------------------------------------


def _is_api(url: str, path_suffix: str) -> bool:
    """A dashboard API call to ``*path_suffix`` (not a dashboard page)."""
    path = urlparse(url).path
    return path.endswith(path_suffix) and not path.startswith('/dashboard/')


def _api_calls(page: Page,
               path_suffix: str,
               method: str = 'GET',
               since: int = 0) -> List[str]:
    return [
        r.url
        for r in page.recorder.requests[since:]  # type: ignore[attr-defined]
        if r.method == method and _is_api(r.url, path_suffix)
    ]


def _row(page: Page, name: str):
    """The Workspaces table row whose name button is ``name``."""
    return page.get_by_role('row').filter(
        has=page.get_by_role('button', name=name, exact=True))


def _count_button(page: Page, name: str, column: int):
    """The count button in the Running Clusters (1) or Jobs (2) column."""
    return _row(page,
                name).get_by_role('cell').nth(column).get_by_role('button')


def _listed_names(page: Page) -> set:
    texts = page.locator('tbody tr td:first-child > button').all_inner_texts()
    return {t.strip() for t in texts if t.strip()}


def _wait_post(page: Page,
               path_suffix: str,
               since: int,
               what: str,
               timeout_s: float = 15) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        posts = page.recorder.posts(path_suffix,
                                    since)  # type: ignore[attr-defined]
        if posts:
            return posts[-1]
        page.wait_for_timeout(200)
    raise AssertionError(f'{what}: no POST {path_suffix} was sent')


def _wait_until(cond: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.3)
    return False


def _set_yaml(page: Page, text: str) -> None:
    """Replace the YAML editor's content (CodeMirror)."""
    editor = page.locator('.cm-content')
    expect(editor, 'the YAML editor must be shown').to_be_visible()
    editor.click()
    page.keyboard.press('ControlOrMeta+a')
    page.keyboard.insert_text(text)


def _jobs_table_requests(page: Page, since: int) -> List[Dict[str, Any]]:
    return [
        b for b in page.recorder.posts(JOBS_QUEUE,
                                       since)  # type: ignore[attr-defined]
        if 'page' in b and b.get('limit') != 1
    ]


# --- twist helpers -------------------------------------------------------------


def _rewrite_async_result(path_suffix: str, fn: Callable[[Any], Any]):
    """res twist: rewrite the result of an async API call.

    The call returns a request id and the result comes from /api/get.
    Remember the ids of calls to ``*path_suffix`` and rewrite only their
    results."""

    def apply(context: BrowserContext) -> None:
        ids = set()

        def on_call(route: Route) -> None:
            resp = route.fetch()
            rid = resp.headers.get('x-skypilot-request-id')
            if rid:
                ids.add(rid)
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            resp = route.fetch()
            rid = parse_qs(urlparse(route.request.url).query).get(
                'request_id', [''])[0]
            if rid not in ids or resp.status != 200:
                route.fulfill(response=resp)
                return
            data = resp.json()
            if data.get('return_value'):
                value = json.loads(data['return_value'])
                data['return_value'] = json.dumps(fn(value))
            route.fulfill(response=resp, json=data)

        context.route(lambda url: _is_api(url, path_suffix), on_call)
        context.route('**/api/get?*', on_get)

    return apply


def _drop_ws_field(field: str):
    """The workspaces result loses ``field`` from every workspace."""

    def fn(workspaces):
        return {
            wid: {k: v for k, v in (cfg or {}).items() if k != field
                 } for wid, cfg in workspaces.items()
        }

    return fn


def _drop_private_workspaces(workspaces):
    return {
        wid: cfg
        for wid, cfg in workspaces.items()
        if not (cfg or {}).get('private')
    }


def _drop_one_test_workspace(workspaces):
    """The workspaces result loses one workspace a test created."""
    out = dict(workspaces)
    for wid, cfg in workspaces.items():
        if re.match(r't[0-9a-f]{6}-', (cfg or {}).get('name') or ''):
            del out[wid]
            break
    return out


def _clusters_lose_workspace(clusters):
    """Cluster records come back without their workspace."""
    return [{**c, 'workspace': None} for c in clusters]


_AS_ADMIN = rewrite_response('/users/role', lambda d, b: {**d, 'role': 'admin'})


def _swallow_count_click(column: int) -> Callable[[BrowserContext], None]:
    """dom twist: clicks on a workspace's count button do nothing."""
    return run_script(f"""
      document.addEventListener('click', (e) => {{
        const cell = e.target.closest('td');
        if (!cell || !e.target.closest('button')) return;
        const row = cell.parentElement;
        if (row && row.children[{column}] === cell &&
            location.pathname.endsWith('/workspaces')) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """)


# --- tests ---------------------------------------------------------------------


@with_twists(
    Twist('srv-workspaces-status-own-clusters-only',
          'srv',
          expect=r'Running Clusters for .* must count 2'),
    Twist('srv-workspaces-jobs-own-only',
          'srv',
          expect=r'Jobs for .* must count 1 active job'),
    Twist('res-workspaces-private-flag-dropped',
          'res',
          expect=r'must be badged Private',
          apply=_rewrite_async_result('/workspaces',
                                      _drop_ws_field('private'))),
)
def test_workspace_list_shows_counts_and_visibility(make_user, make_workspace,
                                                    seed_cluster, seed_job,
                                                    open_as, uid, twist):
    """W-01: columns, Private/Public badges, and per-workspace counts that
    include other users' clusters and jobs."""
    admin = make_user('admin', role='admin')
    owner = make_user('owner')
    alpha = make_workspace('alpha', private=True, allowed_users=[owner.name])
    beta = make_workspace('beta')
    seed_cluster(f'{uid}-up-a', owner.user_id, status='UP', workspace=alpha.id)
    seed_cluster(f'{uid}-up-b', owner.user_id, status='UP', workspace=alpha.id)
    seed_cluster(f'{uid}-stopped',
                 owner.user_id,
                 status='STOPPED',
                 workspace=alpha.id)
    seed_job(f'{uid}-running',
             owner.user_id,
             status='RUNNING',
             workspace=alpha.id)
    seed_job(f'{uid}-done',
             owner.user_id,
             status='SUCCEEDED',
             workspace=alpha.id)

    page = open_as(admin, '/dashboard/workspaces')

    for header in ('Workspace', 'Running Clusters', 'Jobs', 'Enabled infra',
                   'Actions'):
        expect(
            page.get_by_role('columnheader', name=re.compile(rf'^{header}')),
            f'the Workspaces table must have a "{header}" column',
        ).to_be_visible()
    expect(_row(page, alpha.name),
           f'workspace {alpha.name} must be listed').to_be_visible()
    expect(
        _row(page, alpha.name).get_by_text('Private', exact=True),
        f'{alpha.name} (private: true) must be badged Private').to_be_visible()
    expect(
        _row(page, beta.name).get_by_text('Public', exact=True),
        f'{beta.name} must be badged Public').to_be_visible()
    # Two UP clusters run; the STOPPED one does not count. All belong to
    # another user.
    expect(
        _count_button(page, alpha.name, 1),
        f'Running Clusters for {alpha.name} must count 2 (other users\' '
        'clusters included)').to_have_text('2')
    expect(
        _count_button(page, alpha.name, 2),
        f'Jobs for {alpha.name} must count 1 active job (the finished one '
        'does not count)').to_have_text('1')
    expect(_count_button(page, beta.name, 1),
           f'Running Clusters for {beta.name} must be 0').to_have_text('0')
    expect(_count_button(page, beta.name, 2),
           f'Jobs for {beta.name} must be 0').to_have_text('0')


@with_twists(
    Twist('dom-workspaces-refetch-per-keystroke',
          'dom',
          expect=r'must not refetch while typing',
          apply=run_script("""
            document.addEventListener('input', (e) => {
              if (e.target.placeholder === 'Filter workspaces') {
                const r = [...document.querySelectorAll('button')]
                  .find(b => b.innerText.trim() === 'Refresh');
                if (r) r.click();
              }
            }, true);
          """)),
    Twist('dom-workspaces-no-filter-box',
          'dom',
          expect=r'"Filter workspaces" box must be present',
          apply=remove_element('input[placeholder="Filter workspaces"]')),
)
def test_filter_workspaces_is_client_side(make_user, make_workspace, open_as,
                                          twist):
    """W-02: the filter box narrows the table without any request."""
    admin = make_user('admin', role='admin')
    alpha = make_workspace('alpha', private=True, allowed_users=[admin.name])
    beta = make_workspace('beta')

    page = open_as(admin, '/dashboard/workspaces')
    box = page.get_by_placeholder('Filter workspaces')
    expect(box, '"Filter workspaces" box must be present').to_be_visible()
    expect(_row(page, beta.name),
           f'workspace {beta.name} must be listed').to_be_visible()

    since = page.recorder.mark()
    # 450 ms per key is longer than any debounce that would refetch mid-word.
    box.press_sequentially(alpha.name, delay=450)
    page.wait_for_timeout(1500)

    refetches = (_api_calls(page, '/workspaces', 'GET', since) +
                 _api_calls(page, '/check', 'POST', since))
    assert not refetches, ('the Workspaces table must not refetch while '
                           f'typing, got {refetches}')
    expect(_row(page, alpha.name),
           f'{alpha.name} must match the filter').to_be_visible()
    expect(_row(page, beta.name),
           f'{beta.name} must be filtered out').to_have_count(0)
    box.fill('private')
    expect(_row(page, alpha.name),
           'filtering by "private" must keep the private workspace'
          ).to_be_visible()
    expect(
        _row(page, beta.name),
        'filtering by "private" must drop the public workspace').to_have_count(
            0)


@with_twists(
    Twist('dom-workspaces-cluster-count-dead',
          'dom',
          expect=r'the cluster count must open the Clusters page filtered',
          apply=_swallow_count_click(1)),
    Twist('res-workspaces-clusters-lose-workspace',
          'res',
          expect=r'Running Clusters for .* must be 1',
          apply=_rewrite_async_result('/status', _clusters_lose_workspace)),
)
def test_cluster_count_links_to_filtered_clusters(make_user, make_workspace,
                                                  seed_cluster, open_as, uid,
                                                  twist):
    """W-03: the Running Clusters count opens Clusters filtered by the
    workspace name, with the filter chip set."""
    user = make_user('user')
    alpha = make_workspace('alpha')
    beta = make_workspace('beta')
    # The link lands on "My Clusters", so the clusters belong to the viewer.
    in_alpha = seed_cluster(f'{uid}-in-alpha', user.user_id, workspace=alpha.id)
    in_beta = seed_cluster(f'{uid}-in-beta', user.user_id, workspace=beta.id)

    page = open_as(user, '/dashboard/workspaces')
    count = _count_button(page, alpha.name, 1)
    expect(count,
           f'Running Clusters for {alpha.name} must be 1').to_have_text('1')
    count.click()

    expect(
        page, 'the cluster count must open the Clusters page filtered by '
        f'workspace={alpha.name}').to_have_url(
            re.compile(r'/dashboard/clusters\?(.*&)?workspace=' +
                       re.escape(alpha.name) + r'(&|$)'))
    expect(
        page.get_by_text(alpha.name, exact=True).first,
        'the Clusters page must show a Workspace filter chip with the name'
    ).to_be_visible()
    expect(
        page.get_by_role('row').filter(has_text=in_alpha),
        f'cluster {in_alpha} in {alpha.name} must be listed').to_be_visible()
    expect(
        page.get_by_role('row').filter(has_text=in_beta),
        f'cluster {in_beta} in another workspace must be filtered out'
    ).to_have_count(0)


@with_twists(
    Twist('srv-workspaces-jobs-ignore-workspace-match',
          'srv',
          expect=r'job .* in another workspace must not be listed'),
    Twist(
        'req-workspaces-jobs-no-workspace-match',
        'req',
        expect=r'the jobs table must ask for workspace_match',
        apply=rewrite_request(
            JOBS_QUEUE,
            lambda b: {k: v for k, v in b.items() if k != 'workspace_match'})),
    Twist('dom-workspaces-job-count-dead',
          'dom',
          expect=r'the job count must open the Jobs page filtered',
          apply=_swallow_count_click(2)),
)
def test_job_count_links_to_filtered_jobs(make_user, make_workspace, seed_job,
                                          open_as, uid, twist):
    """W-04: the Jobs count opens Managed Jobs filtered by the workspace
    name, and the table asks the server for that workspace only."""
    user = make_user('user')
    alpha = make_workspace('alpha')
    beta = make_workspace('beta')
    seed_job(f'{uid}-in-alpha', user.user_id, workspace=alpha.id)
    seed_job(f'{uid}-in-beta', user.user_id, workspace=beta.id)

    page = open_as(user, '/dashboard/workspaces')
    count = _count_button(page, alpha.name, 2)
    expect(count, f'Jobs for {alpha.name} must be 1').to_have_text('1')
    since = page.recorder.mark()
    count.click()

    expect(
        page, 'the job count must open the Jobs page filtered by '
        f'workspace={alpha.name}').to_have_url(
            re.compile(r'/dashboard/jobs\?(.*&)?workspace=' +
                       re.escape(alpha.name) + r'(&|$)'))
    expect(page.get_by_role('link', name=f'{uid}-in-alpha', exact=True),
           f'job {uid}-in-alpha must be listed').to_be_visible()
    bodies = _jobs_table_requests(page, since)
    assert bodies and all(
        b.get('workspace_match') == alpha.name for b in bodies), (
            f'the jobs table must ask for workspace_match={alpha.name}, got '
            f'{bodies}')
    expect(page.get_by_role('link', name=f'{uid}-in-beta', exact=True),
           f'job {uid}-in-beta in another workspace must not be listed'
          ).to_have_count(0)


@with_twists(
    Twist('srv-workspaces-create-drops-access',
          'srv',
          expect=r'must be badged Private'),
    Twist('req-workspaces-create-drops-allowed-users',
          'req',
          expect=r'the create request must carry',
          apply=rewrite_request(
              '/workspaces/create', lambda b: {
                  **b, 'config': {
                      k: v
                      for k, v in b.get('config', {}).items()
                      if k != 'allowed_users'
                  }
              })),
    Twist('dom-workspaces-create-button-dead',
          'dom',
          expect=r'Create Workspace must open /dashboard/workspace/new',
          apply=swallow_click('Create Workspace')),
    Twist('dom-workspaces-yaml-error-hidden',
          'dom',
          expect=r'must explain that the top-level key must stay',
          apply=run_script("""
            // Hide the editor's error box (ErrorDisplay) for a wrong key.
            new MutationObserver(() => document.querySelectorAll('.bg-red-50')
              .forEach(e => {
                if (/top-level key must stay/.test(e.textContent))
                  e.style.display = 'none';
              })).observe(document, { childList: true, subtree: true });
          """)),
    Twist('dom-workspaces-apply-not-disabled',
          'dom',
          expect=r'Apply must stay disabled while the YAML is invalid',
          apply=run_script("""
            const enable = () => document.querySelectorAll('button')
              .forEach(b => {
                if (b.innerText.trim() === 'Apply' && b.disabled)
                  b.disabled = false;
              });
            new MutationObserver(enable).observe(document, {
              childList: true, subtree: true, attributes: true,
              attributeFilter: ['disabled'] });
          """)),
    Twist('src-workspaces-yaml-key-unchecked',
          'src',
          expect=r'must explain that the top-level key must stay',
          patch='twist_patches/workspaces-yaml-key-unchecked.patch'),
)
def test_create_private_workspace_through_wizard(make_user, make_workspace,
                                                 open_as, uid, twist):
    """W-05, W-06, W-07: Create Workspace -> name -> Next -> YAML. A wrong
    top-level key blocks Apply without a request; a valid private YAML
    creates the workspace and lands on its page (URL by id, title by name)."""
    admin = make_user('admin', role='admin')
    member = make_user('member')
    name = f'{uid}-new'
    make_workspace.track(name)  # type: ignore[attr-defined]

    page = open_as(admin, '/dashboard/workspaces')
    page.get_by_role('button', name='Create Workspace').click()
    expect(page,
           'Create Workspace must open /dashboard/workspace/new').to_have_url(
               re.compile(r'/dashboard/workspace/new$'))
    page.get_by_placeholder('Enter workspace name').fill(name)
    page.get_by_role('button', name='Next: Configure Workspace').click()
    expect(page.get_by_text('New Workspace YAML'),
           'Next must open the YAML step').to_be_visible()
    expect(page.get_by_text('Example configuration:'),
           'the YAML step must show an example configuration').to_be_visible()

    since = page.recorder.mark()
    _set_yaml(page, 'kubernetes:\n  allowed_contexts:\n  - e2e-ctx\n')
    expect(
        page.get_by_text(f'The top-level key must stay "{name}" '
                         '(found "kubernetes")'),
        'the editor must explain that the top-level key must stay the '
        'workspace name').to_be_visible()
    expect(
        page.get_by_role('button', name='Apply'),
        'Apply must stay disabled while the YAML is invalid').to_be_disabled()
    page.wait_for_timeout(500)
    assert not page.recorder.posts(
        '/workspaces/create', since), (  # type: ignore[attr-defined]
            'an invalid YAML must not send a create request')

    _set_yaml(
        page, f'{name}:\n  private: true\n  allowed_users:\n  - {member.name}\n'
        '  kubernetes:\n    allowed_contexts:\n    - e2e-ctx\n')
    apply = page.get_by_role('button', name='Apply')
    expect(apply, 'Apply must be enabled for a valid YAML').to_be_enabled()
    apply.click()
    body = _wait_post(page, '/workspaces/create', since,
                      'Apply must send a create request')
    # Let the server finish creating it, even when an assertion below fails,
    # so the fixture finds and deletes it.
    _wait_until(lambda: seed_workspaces.workspace_id(name) is not None, 60)
    expected = {
        'workspace_name': name,
        'config': {
            'private': True,
            'allowed_users': [member.name],
            'kubernetes': {
                'allowed_contexts': ['e2e-ctx']
            },
        },
    }
    assert body == expected, (
        f'the create request must carry the YAML config: expected {expected}, '
        f'got {body}')

    expect(page, 'a created workspace must open its page, by id').to_have_url(
        re.compile(r'/dashboard/workspaces/ws-[0-9a-f]+$'), timeout=30_000)
    wid = page.url.rsplit('/', 1)[-1]
    assert seed_workspaces.workspace_name(wid) == name, (
        f'the page URL must use the new workspace\'s id, got {wid}')
    expect(page.get_by_text(f'ID: {wid}'),
           'the page must show the id beside the name').to_be_visible()
    expect(page.get_by_role('link', name=name, exact=True),
           f'the breadcrumb must show the workspace name {name}, not its id'
          ).to_be_visible()
    expect(page.get_by_text('Private', exact=True),
           'the new workspace must be badged Private').to_be_visible()
    allowed = page.get_by_text(re.compile(r'^Allowed Users \(\d+\)$'))
    expect(allowed,
           'a private workspace must list its allowed users').to_be_visible()
    for user in (member.name, os.environ['E2E_ADMIN_USER']):
        expect(
            allowed.locator('xpath=..').get_by_text(user, exact=True),
            f'Allowed Users must list {user}').to_be_visible()
    stored = seed_workspaces.config_workspaces()[wid]
    assert stored.get('private') is True and stored.get('allowed_users') == [
        member.name
    ], f'the server must store the workspace private for {member.name}, got {stored}'


@with_twists(
    Twist('res-workspaces-name-dropped',
          'res',
          expect=r'must show the workspace name',
          apply=_rewrite_async_result('/workspaces', _drop_ws_field('name'))),
    Twist('res-workspaces-clusters-lose-workspace-name',
          'res',
          expect=r'Workspace column must show the name',
          apply=_rewrite_async_result(
              '/status', lambda cs: [{
                  **c, 'workspace_name': c.get('workspace')
              } for c in cs])),
    Twist('src-workspaces-name-not-returned',
          'src',
          expect=r'must show the workspace name',
          patch='twist_patches/workspaces-name-not-returned.patch'),
)
def test_detail_page_url_uses_id_and_shows_name(make_user, make_workspace,
                                                seed_cluster, open_as, uid,
                                                twist):
    """W-08: the detail page is addressed by id and shows the name in the
    breadcrumb, card title and tab title; the Clusters table shows the
    workspace name too."""
    admin = make_user('admin', role='admin')
    alpha = make_workspace('alpha')
    cluster = seed_cluster(f'{uid}-c', admin.user_id, workspace=alpha.id)

    page = open_as(admin, f'/dashboard/workspaces/{alpha.id}')
    expect(page.get_by_role('link', name=alpha.name, exact=True),
           'the breadcrumb must show the workspace name').to_be_visible()
    expect(page.get_by_text(re.compile(rf'^Workspace:\s*{alpha.name}')),
           'the card title must show the workspace name').to_be_visible()
    expect(page, 'the tab title must show the workspace name').to_have_title(
        f'Workspace: {alpha.name} | SkyPilot Dashboard')
    # The id is shown only beside a name that differs from it.
    expect(page.get_by_text(f'ID: {alpha.id}'),
           'the detail page must show the workspace id').to_be_visible()

    page.goto('/dashboard/clusters')
    row = page.get_by_role('row').filter(has_text=cluster)
    expect(row, f'cluster {cluster} must be listed').to_be_visible()
    expect(
        row.get_by_role('cell', name=alpha.name, exact=True),
        'the Workspace column must show the name, not the id').to_be_visible()


@with_twists(
    Twist('src-workspaces-private-visible-to-all',
          'src',
          expect=r'must not see private workspace',
          patch='twist_patches/workspaces-private-visible-to-all.patch'),
    Twist('res-workspaces-private-hidden-from-members',
          'res',
          expect=r'a member must see private workspace',
          apply=_rewrite_async_result('/workspaces', _drop_private_workspaces)),
)
def test_private_workspace_visible_only_to_members(make_user, make_workspace,
                                                   open_as, twist):
    """W-09, RM-01 (viewer): a private workspace is listed for its members
    and hidden from other users and viewers; public ones are listed for
    everyone."""
    member = make_user('member')
    outsider = make_user('outsider')
    viewer = make_user('viewer', role='viewer')
    private = make_workspace('private',
                             private=True,
                             allowed_users=[member.name])
    public = make_workspace('public')

    page = open_as(member, '/dashboard/workspaces')
    expect(_row(page, public.name),
           f'a member must see public workspace {public.name}').to_be_visible()
    expect(
        _row(page, private.name),
        f'a member must see private workspace {private.name}').to_be_visible()

    for ident in (outsider, viewer):
        page = open_as(ident, '/dashboard/workspaces')
        expect(
            _row(page, public.name),
            f'{ident.role} {ident.name} must see public workspace '
            f'{public.name}').to_be_visible()
        expect(
            _row(page, private.name),
            f'{ident.role} {ident.name} is not a member and must not see '
            f'private workspace {private.name}').to_have_count(0)


@with_twists(
    Twist('res-workspaces-role-says-admin',
          'res',
          expect=r'a non-admin must get a Permission Denied dialog',
          apply=_AS_ADMIN),
    Twist('dom-workspaces-denial-dialog-missing',
          'dom',
          expect=r'a non-admin must get a Permission Denied dialog',
          apply=run_script("""
            new MutationObserver(() => document
              .querySelectorAll('[role="dialog"]').forEach(d => {
                if (/Permission Denied/.test(d.textContent)) d.remove();
              })).observe(document, { childList: true, subtree: true });
          """)),
    Twist('src-workspaces-role-reports-admin',
          'src',
          expect=r'a non-admin must get a Permission Denied dialog',
          patch='twist_patches/workspaces-role-reports-admin.patch'),
)
def test_non_admin_delete_is_denied_without_request(make_user, make_workspace,
                                                    open_as, twist):
    """W-10, RM-01 (user): a non-admin sees Delete but gets "Permission
    Denied" and no delete request; Create is denied the same way and Rename
    is not offered."""
    user = make_user('user')
    alpha = make_workspace('alpha')

    page = open_as(user, '/dashboard/workspaces')
    row = _row(page, alpha.name)
    expect(row, f'workspace {alpha.name} must be listed').to_be_visible()
    since = page.recorder.mark()
    row.get_by_role('button', name='Delete workspace').click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_text('Permission Denied'),
           'a non-admin must get a Permission Denied dialog').to_be_visible()
    expect(
        dialog.get_by_text(f'{user.name} is logged in as non-admin and cannot '
                           'delete workspace.'),
        'the dialog must say who is denied and why').to_be_visible()
    page.wait_for_timeout(500)
    assert not page.recorder.posts(
        '/workspaces/delete', since), (  # type: ignore[attr-defined]
            'a non-admin clicking Delete must not send a delete request')
    dialog.get_by_role('button', name='OK').click()
    expect(row.get_by_role('button', name='Rename workspace'),
           'a non-admin must not be offered Rename').to_have_count(0)

    page.get_by_role('button', name='Create Workspace').click()
    expect(
        dialog.get_by_text(f'{user.name} is logged in as non-admin and cannot '
                           'create workspace.'),
        'a non-admin clicking Create Workspace must get a Permission Denied '
        'dialog').to_be_visible()
    expect(page,
           'a denied Create must stay on the Workspaces page').to_have_url(
               re.compile(r'/dashboard/workspaces$'))
    assert seed_workspaces.workspace_id(
        alpha.name) == alpha.id, (f'workspace {alpha.name} must still exist')


@with_twists(
    Twist('srv-workspaces-delete-by-name',
          'srv',
          expect=r'the deleted workspace must disappear from the list'),
    Twist('dom-workspaces-confirm-delete-dead',
          'dom',
          expect=r'confirming must send a delete request',
          apply=run_script("""
            document.addEventListener('click', (e) => {
              const b = e.target.closest('button');
              if (b && b.innerText.trim() === 'Delete' &&
                  b.closest('[role="dialog"]')) {
                e.stopImmediatePropagation();
                e.preventDefault();
              }
            }, true);
          """)),
    Twist('req-workspaces-delete-sends-name',
          'req',
          expect=r'the delete request must name the workspace by id',
          apply=rewrite_request(
              '/workspaces/delete', lambda b: {
                  **b, 'workspace_name': seed_workspaces.workspace_name(b[
                      'workspace_name'])
              })),
)
def test_admin_deletes_workspace(make_user, make_workspace, open_as, twist):
    """W-11, RM-01 (admin): an admin is offered Rename and Delete; Delete asks
    for confirmation by name, sends the id, and the row goes away."""
    admin = make_user('admin', role='admin')
    alpha = make_workspace('alpha')

    page = open_as(admin, '/dashboard/workspaces')
    row = _row(page, alpha.name)
    expect(row, f'workspace {alpha.name} must be listed').to_be_visible()
    expect(row.get_by_role('button', name='Rename workspace'),
           'an admin must be offered Rename').to_have_count(1)
    expect(page.get_by_role('button', name='Create Workspace'),
           'an admin must be offered Create Workspace').to_be_enabled()
    row.get_by_role('button', name='Delete workspace').click()
    dialog = page.get_by_role('dialog')
    expect(
        dialog.get_by_text(
            re.compile(r'Are you sure you want to delete workspace\s*"\s*' +
                       re.escape(alpha.name) + r'\s*"\?')),
        'the confirmation must name the workspace').to_be_visible()
    since = page.recorder.mark()
    dialog.get_by_role('button', name='Delete', exact=True).click()
    body = _wait_post(page, '/workspaces/delete', since,
                      'confirming must send a delete request')
    assert body.get('workspace_name') == alpha.id, (
        f'the delete request must name the workspace by id ({alpha.id}), got '
        f'{body}')
    # The open dialog hides the table from the accessibility tree, so a
    # role-based row would look gone while the dialog is still up.
    expect(
        dialog, 'the deleted workspace must disappear from the list: its '
        'confirmation must close').to_be_hidden(timeout=30_000)
    expect(_row(page, alpha.name),
           'the deleted workspace must disappear from the list').to_have_count(
               0, timeout=30_000)
    expect(page.get_by_text(f'Workspace "{alpha.name}" deleted successfully!'),
           'a successful delete must be confirmed').to_be_visible()
    assert seed_workspaces.workspace_id(alpha.name) is None, (
        f'the deleted workspace must be gone from the server config')


@with_twists(
    Twist('dom-workspaces-default-delete-enabled',
          'dom',
          expect=r'Delete must be disabled for the default workspace',
          apply=run_script("""
            const enable = () => document
              .querySelectorAll('button[title="Cannot delete default workspace"]')
              .forEach(b => { if (b.disabled) b.disabled = false; });
            new MutationObserver(enable).observe(document, {
              childList: true, subtree: true, attributes: true,
              attributeFilter: ['disabled'] });
          """)),
    Twist('res-workspaces-role-admin-for-everyone',
          'res',
          expect=r'a non-admin must not be offered Rename',
          apply=_AS_ADMIN),
)
def test_default_workspace_cannot_be_deleted_and_rename_is_admin_only(
        make_user, make_workspace, open_as, twist):
    """W-12: the default workspace's Delete is disabled with a reason, it has
    no Rename and no Delete on its page; Rename is offered to admins only."""
    admin = make_user('admin', role='admin')
    user = make_user('user')
    alpha = make_workspace('alpha')

    page = open_as(admin, '/dashboard/workspaces')
    default = _row(page, 'default')
    delete = default.get_by_role('button',
                                 name='Cannot delete default workspace')
    expect(delete, 'the default workspace must explain why it cannot be '
           'deleted').to_be_visible()
    expect(
        delete,
        'Delete must be disabled for the default workspace').to_be_disabled()
    expect(default.get_by_role('button', name='Rename workspace'),
           'the default workspace must not be offered Rename').to_have_count(0)
    expect(
        _row(page, alpha.name).get_by_role('button', name='Rename workspace'),
        'an admin must be offered Rename on other workspaces').to_have_count(1)

    page.goto('/dashboard/workspaces/default')
    expect(page.get_by_text(re.compile(r'^Workspace:\s*default')),
           'the default workspace page must load').to_be_visible()
    expect(page.get_by_role('button', name='Delete', exact=True),
           'the default workspace page must not offer Delete').to_have_count(0)
    expect(page.get_by_role('button', name='Rename workspace'),
           'the default workspace page must not offer Rename').to_have_count(0)

    page = open_as(user, '/dashboard/workspaces')
    expect(_row(page, alpha.name),
           f'workspace {alpha.name} must be listed').to_be_visible()
    expect(page.get_by_role('button', name='Rename workspace'),
           'a non-admin must not be offered Rename').to_have_count(0)


@with_twists(
    Twist('srv-workspaces-update-drops-private',
          'srv',
          expect=r'after a reload the workspace must be badged Private'),
    Twist('req-workspaces-update-drops-allowed-users',
          'req',
          expect=r'the update request must carry',
          apply=rewrite_request(
              '/workspaces/update', lambda b: {
                  **b, 'config': {
                      k: v
                      for k, v in b.get('config', {}).items()
                      if k != 'allowed_users'
                  }
              })),
    Twist('dom-workspaces-apply-dead',
          'dom',
          expect=r'Apply must send an update request',
          apply=swallow_click('Apply')),
)
def test_edit_workspace_yaml_and_apply(make_user, make_workspace, open_as,
                                       twist):
    """W-13: an admin edits the workspace YAML and applies it; the update
    names the workspace by id, keeps its name, and survives a reload."""
    admin = make_user('admin', role='admin')
    member = make_user('member')
    alpha = make_workspace('alpha')

    page = open_as(admin, f'/dashboard/workspaces/{alpha.id}')
    expect(page.get_by_text('Edit Workspace YAML'),
           'an admin must get the YAML editor').to_be_visible()
    expect(page.get_by_text('Public', exact=True),
           'the workspace must start Public').to_be_visible()
    since = page.recorder.mark()
    _set_yaml(
        page, f'{alpha.name}:\n  private: true\n  allowed_users:\n'
        f'  - {member.name}\n')
    page.get_by_role('button', name='Apply').click()
    body = _wait_post(page, '/workspaces/update', since,
                      'Apply must send an update request')
    expected = {
        'private': True,
        'allowed_users': [member.name],
        'name': alpha.name
    }
    assert (body.get('workspace_name') == alpha.id and
            body.get('config') == expected), (
                f'the update request must carry workspace_name={alpha.id} and '
                f'config {expected}, got {body}')
    expect(page.get_by_text('Workspace updated successfully!'),
           'a successful update must be confirmed').to_be_visible()

    page.reload()
    expect(page.get_by_text(f'ID: {alpha.id}'),
           'the workspace page must load again').to_be_visible()
    expect(
        page.get_by_text('Private', exact=True),
        'after a reload the workspace must be badged Private').to_be_visible()
    expect(
        page.get_by_text(member.name, exact=True).first,
        f'after a reload Allowed Users must list {member.name}').to_be_visible(
        )


@with_twists(
    Twist('res-workspaces-one-missing',
          'res',
          expect=r'the admin list must show every workspace',
          apply=_rewrite_async_result('/workspaces', _drop_one_test_workspace)),
    Twist('src-workspaces-private-visible-to-all',
          'src',
          expect=r'a non-member must see exactly',
          patch='twist_patches/workspaces-private-visible-to-all.patch'),
)
@pytest.mark.exclusive
def test_workspace_list_matches_server_config(make_user, make_workspace,
                                              open_as, twist):
    """W-01, RM-01: an admin sees every configured workspace; a non-member
    sees all but the private ones."""
    admin = make_user('admin', role='admin')
    user = make_user('user')
    make_workspace('private', private=True, allowed_users=[admin.name])
    make_workspace('public')

    workspaces = seed_workspaces.config_workspaces()
    every = {seed_workspaces.workspace_name(w, workspaces) for w in workspaces
            } | {'default'}
    visible = {
        seed_workspaces.workspace_name(w, workspaces)
        for w, cfg in workspaces.items()
        if not (cfg or {}).get('private') or user.name in (
            (cfg or {}).get('allowed_users') or [])
    } | {'default'}

    for ident, expected, what in (
        (admin, every, 'the admin list must show every workspace'),
        (user, visible, 'a non-member must see exactly the public workspaces'),
    ):
        page = open_as(ident, '/dashboard/workspaces')
        expect(_row(page, 'default'),
               'the default workspace must be listed').to_be_visible()
        names = _listed_names(page)
        deadline = time.time() + 15
        while names != expected and time.time() < deadline:
            page.wait_for_timeout(300)
            names = _listed_names(page)
        assert names == expected, (
            f'{what}: expected {sorted(expected)}, got {sorted(names)}')
