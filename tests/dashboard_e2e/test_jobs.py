"""Managed Jobs list and job detail page.

The list page asks the server for one page of the viewer's jobs
(``/jobs/queue/v2``) with the status, workspace, name and user filters the
page and its URL carry, and renders status chips, filter chips, pagination,
row links and row actions. The job detail page asks for one job and its tree,
streams the task and controller logs (``/jobs/logs``) and downloads them as
a zip (``/jobs/download_logs``).

Every test here owns its users and jobs and asserts only on those: the table
is scoped to the test's own user ("My Jobs"), or to the test's ``uid`` name
prefix under "All Jobs", so all of them run in parallel.
"""
import io
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional
import zipfile

from framework.fixtures import wait_ready
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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
import pytest
import seed_jobs

JOBS_QUEUE = '/jobs/queue/v2'
JOBS_LOGS = '/jobs/logs'
JOBS_DOWNLOAD_LOGS = '/jobs/download_logs'

ACTIVE_STATUSES = {
    'PENDING', 'RUNNING', 'RECOVERING', 'SUBMITTED', 'STARTING', 'CANCELLING'
}
MORE_STATUSES = [
    'PENDING', 'SUBMITTED', 'RECOVERING', 'CANCELLING', 'CANCELLED', 'FAILED',
    'FAILED_SETUP', 'FAILED_PRECHECKS', 'FAILED_NO_RESOURCE',
    'FAILED_CONTROLLER'
]
TASK_LOG = [
    # Colored, the way a task's output usually is: the page must strip it.
    '(head, rank=0, pid=101) \x1b[32me2e hello from head\x1b[0m',
    '(worker1, rank=1, pid=102) e2e hello from worker1',
]
CONTROLLER_LOG = ['e2e controller line one', 'e2e controller line two']

# --- request helpers ---------------------------------------------------------


def _is_jobs_page(body: Dict[str, Any]) -> bool:
    """A jobs-table request: paged, and not the one-row total-count probe."""
    return 'page' in body and body.get('limit') != 1


def _is_listing(body: Dict[str, Any]) -> bool:
    """A jobs-table request or the unpaged one that caches every page."""
    return body.get('limit') != 1 and not body.get('job_ids')


def _is_detail(body: Dict[str, Any]) -> bool:
    return bool(body.get('job_ids')) and bool(body.get('include_tree'))


def _wait_body(page: Page,
               path: str,
               since: int,
               pred: Callable[[Dict[str, Any]], bool],
               message: str,
               timeout_s: float = 15) -> Dict[str, Any]:
    """The latest POST to ``path`` since ``since`` that satisfies ``pred``;
    fails with ``message`` (and the bodies seen) if none comes."""
    deadline = time.time() + timeout_s
    seen: List[Dict[str, Any]] = []
    while time.time() < deadline:
        seen = page.recorder.posts(path, since)
        matching = [b for b in seen if pred(b)]
        if matching:
            return matching[-1]
        page.wait_for_timeout(200)
    raise AssertionError(f'{message}; requests sent: {seen[-3:]}')


def _wait_table_body(page: Page, since: int, pred: Callable[[Dict[str, Any]],
                                                            bool],
                     message: str) -> Dict[str, Any]:
    return _wait_body(page, JOBS_QUEUE, since,
                      lambda b: _is_jobs_page(b) and pred(b), message)


# --- twist helpers -----------------------------------------------------------


def _rewrite_queue_result(pred: Callable[[Dict[str, Any]], bool],
                          fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
    """res twist: rewrite the result of job queries whose body matches.

    Job queries are async: the POST returns a request id and the result comes
    from /api/get. Remember the request ids of matching queries, then rewrite
    those results."""

    def apply(context: BrowserContext) -> None:
        ids = set()

        def on_queue(route: Route) -> None:
            resp = route.fetch()
            if pred(json.loads(route.request.post_data or '{}')):
                ids.add(resp.headers.get('x-skypilot-request-id'))
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            resp = route.fetch()
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in ids or resp.status != 200:
                route.fulfill(response=resp)
                return
            data = resp.json()
            data['return_value'] = json.dumps(
                fn(json.loads(data['return_value'])))
            route.fulfill(response=resp, json=data)

        context.route(f'**{JOBS_QUEUE}', on_queue)
        context.route('**/api/get?*', on_get)

    return apply


def _drop_status_count(status: str):

    def fn(value: Dict[str, Any]) -> Dict[str, Any]:
        value.get('status_counts', {}).pop(status, None)
        return value

    return _rewrite_queue_result(_is_listing, fn)


def _edit_detail_jobs(**fields):

    def fn(value: Dict[str, Any]) -> Dict[str, Any]:
        for job in value.get('jobs', []):
            job.update(fields)
        return value

    return _rewrite_queue_result(_is_detail, fn)


def _swallow_clicks(selector: str):
    """dom twist: clicks on (or inside) elements matching ``selector`` do
    nothing, like a handler that stopped being wired up."""
    return run_script(f"""
      document.addEventListener('click', (e) => {{
        if (e.target.closest({json.dumps(selector)})) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """)


def _swallow_button_starting(prefix: str):
    """dom twist: clicks on buttons whose text starts with ``prefix`` do
    nothing."""
    return run_script(f"""
      document.addEventListener('click', (e) => {{
        const b = e.target.closest('button');
        if (b && b.innerText.trim().startsWith({json.dumps(prefix)})) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """)


def _strip_from_logs(text: str):
    """res twist: the log stream loses ``text`` from every line."""

    def apply(context: BrowserContext) -> None:

        def handler(route: Route) -> None:
            resp = route.fetch()
            route.fulfill(response=resp, body=resp.text().replace(text, ''))

        context.route(f'**{JOBS_LOGS}', handler)

    return apply


def _keep_user_filter(context: BrowserContext) -> None:
    """req twist: once the table has filtered by a user, keep that user filter
    on every later table request, so clearing the chip changes nothing."""
    seen: Dict[str, str] = {}

    def handler(route: Route) -> None:
        body = json.loads(route.request.post_data or '{}')
        if _is_listing(body):
            if body.get('user_match'):
                seen['user'] = body['user_match']
            elif 'user' in seen:
                body['user_match'] = seen['user']
                route.continue_(post_data=json.dumps(body))
                return
        route.continue_()

    context.route(f'**{JOBS_QUEUE}', handler)


# --- page helpers ------------------------------------------------------------


def _jobs_table(page: Page):
    # The managed jobs table comes before the pools table.
    return page.locator('table').first


def _row(page: Page, name: str):
    return _jobs_table(page).get_by_role('row').filter(
        has=page.get_by_role('link', name=name, exact=True))


def _job_link(page: Page, name: str):
    return _jobs_table(page).get_by_role('link', name=name, exact=True)


def _status_chip(page: Page, status: str, count: Optional[int] = None):
    count_re = r'\d+' if count is None else str(count)
    return page.get_by_role('button',
                            name=re.compile(rf'^{status}\s+{count_re}$'))


def _footer(page: Page, start: int, end: int, total: int):
    return page.get_by_text(
        re.compile(rf'^{start}\s*–\s*{end}\s+of\s+{total}$'))


def _download(page: Page, click: Callable[[], None], what: str):
    try:
        with page.expect_download(timeout=60_000) as info:
            click()
        return info.value
    except PlaywrightTimeoutError as e:
        raise AssertionError(f'{what} must download a zip') from e


def _zip_text(download) -> str:
    with open(download.path(), 'rb') as f:
        z = zipfile.ZipFile(io.BytesIO(f.read()))
    return '\n'.join(
        z.read(n).decode('utf-8', 'replace')
        for n in z.namelist()
        if not n.endswith('/'))


def _logs_pane(page: Page, section: str = '#logs-section'):
    return page.locator(f'{section} [aria-label="job-logs"]')


@pytest.fixture
def job_logs(seed_job):
    """Write log files for this test's jobs; removed before the jobs are
    (this fixture depends on seed_job, so it is torn down first)."""
    del seed_job
    ids: List[int] = []

    class _Logs:

        def task(self, job_id: int, lines: List[str]) -> None:
            ids.append(job_id)
            seed_jobs.write_task_log(job_id, lines)

        def controller(self, job_id: int, lines: List[str]) -> None:
            ids.append(job_id)
            seed_jobs.write_controller_log(job_id, lines)

    yield _Logs()
    for job_id in ids:
        seed_jobs.remove_logs(job_id)


# --- list page ---------------------------------------------------------------


@with_twists(
    Twist('srv-jobs-queue-short-page',
          'srv',
          expect=r'every job of mine must be listed'),
    Twist('res-status-counts-missing-running',
          'res',
          expect=r'the RUNNING chip must count 2',
          apply=_drop_status_count('RUNNING')),
    Twist('dom-no-row-download-logs',
          'dom',
          expect=r'every row must offer Download logs',
          apply=remove_element('button[title="Download logs"]')),
)
def test_jobs_table_lists_my_jobs_with_columns_and_counts(
        make_user, seed_job, open_as, uid, twist):
    """J-01: columns, status chips with counts, row actions, pools table."""
    owner = make_user('owner')
    statuses = ['RUNNING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PENDING']
    names = [f'{uid}-j{i}-{s.lower()}' for i, s in enumerate(statuses)]
    for name, status in zip(names, statuses):
        seed_job(name, owner.user_id, status=status)

    page = open_as(owner, '/dashboard/jobs')

    body = _wait_table_body(page, 0, lambda b: True,
                            'the jobs table must ask for a page of jobs')
    assert (body.get('page'), body.get('limit'),
            body.get('user_match')) == (1, 10, owner.name), (
                'the first table request must ask for page 1 of 10 of my jobs '
                f'({owner.name}), got {body}')
    assert _wait_body(page, '/jobs/pool_status', 0, lambda b: True,
                      'the page must ask for the pools') is not None
    table = _jobs_table(page)
    for header in ('ID', 'Name', 'User', 'Submitted', 'Duration', 'Status',
                   'Infra', 'Resources', 'Recoveries', 'Details', 'Logs'):
        expect(
            table.get_by_role('columnheader', name=re.compile(rf'^{header}\b')),
            f'the jobs table must have a {header} column').to_be_visible()
    for name in names:
        expect(_job_link(page, name),
               f'every job of mine must be listed: {name}').to_be_visible()
    expect(table.locator('tbody tr'),
           'every job of mine must be listed, and nothing else').to_have_count(
               len(names))
    expect(table.locator('button[title="Download logs"]'),
           'every row must offer Download logs').to_have_count(len(names))
    expect(_status_chip(page, 'RUNNING', 2),
           'the RUNNING chip must count 2 of my jobs').to_be_visible()
    expect(_status_chip(page, 'SUCCEEDED', 1),
           'the SUCCEEDED chip must count 1 of my jobs').to_be_visible()
    expect(_status_chip(page, 'STARTING', 0),
           'the STARTING chip must count 0 of my jobs').to_be_visible()
    expect(
        page.get_by_role('button', name=re.compile(r'^More\s+2$')),
        'More must count the 2 jobs in other statuses (FAILED, PENDING)',
    ).to_be_visible()
    expect(page.get_by_text('No pools found'),
           'the pools table must be shown').to_be_visible()


@with_twists(
    Twist('srv-jobs-queue-ignores-statuses',
          'srv',
          expect=r'must hide the SUCCEEDED job'),
    Twist('src-jobs-queue-ignores-statuses',
          'src',
          expect=r'must hide the SUCCEEDED job',
          patch='twist_patches/jobs-queue-ignores-statuses.patch'),
    Twist('dom-running-chip-dead',
          'dom',
          expect=r'clicking RUNNING must put status=RUNNING in the URL',
          apply=_swallow_button_starting('RUNNING')),
)
def test_running_chip_filters_table_and_survives_reload(make_user, seed_job,
                                                        open_as, uid, twist):
    """J-02 and the status=RUNNING URL state."""
    owner = make_user('owner')
    running, succeeded = f'{uid}-running', f'{uid}-succeeded'
    seed_job(running, owner.user_id, status='RUNNING')
    seed_job(succeeded, owner.user_id, status='SUCCEEDED')

    page = open_as(owner, '/dashboard/jobs')
    expect(_job_link(page, succeeded),
           'my SUCCEEDED job must be listed before filtering').to_be_visible()
    since = page.recorder.mark()
    _status_chip(page, 'RUNNING').click()

    expect(page,
           'clicking RUNNING must put status=RUNNING in the URL').to_have_url(
               re.compile(r'[?&]status=RUNNING(&|$)'))
    _wait_table_body(page, since, lambda b: b.get('statuses') == ['RUNNING'],
                     'the RUNNING chip must ask for statuses=["RUNNING"]')
    expect(_job_link(page, running),
           'the RUNNING chip must list the RUNNING job').to_be_visible()
    expect(_job_link(page, succeeded),
           'the RUNNING chip must hide the SUCCEEDED job').to_have_count(0)

    since = page.recorder.mark()
    page.reload()
    wait_ready(page)
    _wait_table_body(page, since, lambda b: b.get('statuses') == ['RUNNING'],
                     'a status=RUNNING link must ask for RUNNING jobs only')
    expect(_job_link(page, running),
           'a status=RUNNING link must list the RUNNING job').to_be_visible()
    expect(_job_link(page, succeeded),
           'a status=RUNNING link must hide the SUCCEEDED job').to_have_count(0)


@with_twists(
    Twist('res-status-counts-missing-failed',
          'res',
          expect=r'More must count 1 FAILED job',
          apply=_drop_status_count('FAILED')),
    Twist('srv-jobs-queue-first-status-only',
          'srv',
          expect=r'selecting FAILED and PENDING must list the PENDING job'),
    Twist('dom-more-menu-dead',
          'dom',
          expect=r'More must list PENDING',
          apply=_swallow_clicks('button[aria-haspopup="true"]')),
)
def test_more_menu_lists_other_statuses_and_selects_several(
        make_user, seed_job, open_as, uid, twist):
    """J-03: the More menu holds the other statuses, with counts, and several
    can be selected at once."""
    owner = make_user('owner')
    failed, pending, running = (f'{uid}-failed', f'{uid}-pending',
                                f'{uid}-running')
    seed_job(failed, owner.user_id, status='FAILED')
    seed_job(pending, owner.user_id, status='PENDING')
    seed_job(running, owner.user_id, status='RUNNING')

    page = open_as(owner, '/dashboard/jobs')
    expect(_job_link(page, running),
           'my RUNNING job must be listed before filtering').to_be_visible()
    page.get_by_role('button', name=re.compile(r'^More\b')).click()
    for status in MORE_STATUSES:
        expect(_status_chip(page, status),
               f'More must list {status}').to_be_visible()
    expect(_status_chip(page, 'FAILED', 1),
           'More must count 1 FAILED job').to_be_visible()
    expect(_status_chip(page, 'PENDING', 1),
           'More must count 1 PENDING job').to_be_visible()

    since = page.recorder.mark()
    _status_chip(page, 'FAILED').click()
    _status_chip(page, 'PENDING').click()
    # Close the menu, so the chips below are the only status buttons left.
    page.get_by_text('Statuses:').click()
    expect(_status_chip(page, 'CANCELLED'),
           'More must close on a click outside it').to_have_count(0)

    expect(page,
           'selecting FAILED and PENDING must put both in the URL').to_have_url(
               re.compile(r'[?&]status=FAILED(%2C|,)PENDING(&|$)'))
    _wait_table_body(
        page, since,
        lambda b: sorted(b.get('statuses') or []) == ['FAILED', 'PENDING'],
        'selecting FAILED and PENDING must ask for both statuses')
    for status in ('FAILED', 'PENDING'):
        expect(_status_chip(page, status, 1),
               f'the selected {status} must show as a chip').to_be_visible()
    # The unfiltered table listed every job; wait for the filtered one first.
    expect(
        _job_link(page, running),
        'selecting FAILED and PENDING must hide the RUNNING job').to_have_count(
            0)
    expect(_job_link(page, failed),
           'selecting FAILED and PENDING must list the FAILED job'
          ).to_be_visible()
    expect(_job_link(page, pending),
           'selecting FAILED and PENDING must list the PENDING job'
          ).to_be_visible()


@with_twists(
    Twist('dom-active-tab-dead',
          'dom',
          expect=r'Active must switch the URL to status=active',
          apply=swallow_click('Active')),
    Twist('req-active-drops-pending',
          'req',
          expect=r'Active must ask for every active status',
          apply=rewrite_request(
              JOBS_QUEUE, lambda b: {
                  **b, 'statuses': [s for s in b['statuses'] if s != 'PENDING']
              } if 'RUNNING' in (b.get('statuses') or []) else b)),
    Twist('srv-jobs-queue-active-includes-finished',
          'srv',
          expect=r'the SUCCEEDED job must be hidden under Active'),
)
def test_active_tab_and_status_active_link(make_user, seed_job, open_as, uid,
                                           twist):
    """J-04 and the status=active URL state."""
    owner = make_user('owner')
    running, pending, succeeded = (f'{uid}-running', f'{uid}-pending',
                                   f'{uid}-succeeded')
    seed_job(running, owner.user_id, status='RUNNING')
    seed_job(pending, owner.user_id, status='PENDING')
    seed_job(succeeded, owner.user_id, status='SUCCEEDED')

    page = open_as(owner, '/dashboard/jobs')
    expect(_job_link(page, succeeded),
           'my SUCCEEDED job must be listed under All').to_be_visible()
    since = page.recorder.mark()
    page.get_by_role('tab', name='Active').click()

    expect(page, 'Active must switch the URL to status=active').to_have_url(
        re.compile(r'[?&]status=active(&|$)'))
    _wait_table_body(
        page, since, lambda b: set(b.get('statuses') or []) == ACTIVE_STATUSES,
        f'Active must ask for every active status '
        f'{sorted(ACTIVE_STATUSES)}')

    def check_active(where: str) -> None:
        expect(page.get_by_role('tab', name='Active'),
               f'{where}: Active must be selected').to_have_attribute(
                   'aria-selected', 'true')
        # Hidden first: the unfiltered table listed every job.
        expect(_job_link(page, succeeded),
               f'the SUCCEEDED job must be hidden under Active ({where})'
              ).to_have_count(0)
        expect(_job_link(page, running),
               f'the RUNNING job must be listed under Active ({where})'
              ).to_be_visible()
        expect(_job_link(page, pending),
               f'the PENDING job must be listed under Active ({where})'
              ).to_be_visible()

    check_active('after clicking Active')

    since = page.recorder.mark()
    page.goto('/dashboard/jobs?status=active')
    wait_ready(page)
    _wait_table_body(page, since,
                     lambda b: set(b.get('statuses') or []) == ACTIVE_STATUSES,
                     'a status=active link must ask for every active status')
    check_active('opened from a status=active link')


@with_twists(
    Twist('dom-no-workspace-property',
          'dom',
          expect=r'Filter Property must offer Workspace',
          apply=run_script("""
            new MutationObserver(() => {
              document.querySelectorAll('[role="option"]').forEach(o => {
                if (o.innerText.trim() === 'Workspace') o.remove();
              });
            }).observe(document, { childList: true, subtree: true });
          """)),
    Twist('dom-filter-enter-ignored',
          'dom',
          expect=r'Workspace : default chip',
          apply=run_script("""
            document.addEventListener('keydown', (e) => {
              if (e.key === 'Enter' && e.target.placeholder === 'Filter jobs') {
                e.stopImmediatePropagation();
              }
            }, true);
          """)),
    Twist('srv-jobs-queue-wrong-workspace',
          'srv',
          expect=r'my job in workspace default must still be listed'),
)
def test_workspace_filter_chip_and_link(make_user, seed_job, open_as, uid,
                                        twist):
    """J-05 (the filter properties), J-06 (a Workspace filter) and the
    workspace= URL state."""
    owner = make_user('owner')
    name = f'{uid}-train'
    seed_job(name, owner.user_id, status='RUNNING', workspace='default')

    page = open_as(owner, '/dashboard/jobs')
    expect(_job_link(page, name),
           'my job must be listed before filtering').to_be_visible()
    page.get_by_role('combobox', name='Filter Property').click()
    for label in ('Name', 'ID', 'User', 'Workspace', 'Pool', 'Infra', 'Labels'):
        expect(page.get_by_role('option', name=label, exact=True),
               f'Filter Property must offer {label}').to_be_visible()
    page.get_by_role('option', name='Workspace', exact=True).click()
    since = page.recorder.mark()
    box = page.get_by_placeholder('Filter jobs')
    box.fill('default')
    box.press('Enter')

    chip = page.get_by_text(re.compile(r'^Workspace\s*:\s*default$'))
    expect(
        chip,
        'pressing Enter must add a Workspace : default chip').to_be_visible()
    expect(
        page,
        'the Workspace chip must put workspace=default in the URL').to_have_url(
            re.compile(r'[?&]workspace=default(&|$)'))
    _wait_table_body(page, since,
                     lambda b: b.get('workspace_match') == 'default',
                     'the Workspace chip must ask for workspace_match=default')
    expect(_job_link(page, name),
           'my job in workspace default must still be listed').to_be_visible()

    since = page.recorder.mark()
    page.reload()
    wait_ready(page)
    expect(chip, 'a workspace=default link must restore the Workspace chip'
          ).to_be_visible()
    _wait_table_body(page, since,
                     lambda b: b.get('workspace_match') == 'default',
                     'a workspace=default link must filter by workspace')
    expect(_job_link(page, name),
           'my job in workspace default must still be listed after a reload'
          ).to_be_visible()


@with_twists(
    Twist('srv-jobs-queue-limit-capped',
          'srv',
          expect=r'30 per page must (list all 12 jobs|read 1 – 12 of 12)'),
    Twist('res-total-off-by-one',
          'res',
          expect=r'the footer must read "1 – 10 of 12"',
          apply=_rewrite_queue_result(
              _is_listing, lambda v: {
                  **v, 'total': v.get('total', 0) + 1
              })),
    Twist('dom-next-page-dead',
          'dom',
          expect=r'the next page must read "11 – 12 of 12"',
          apply=_swallow_clicks('button:has(path[d="M9 18l6-6-6-6"])')),
)
def test_pagination_and_page_size(make_user, seed_job, open_as, uid, twist):
    """J-07: the footer, the next page and the page size."""
    owner = make_user('owner')
    # Newest last: the table lists the highest id first.
    names = [f'{uid}-p{i:02d}' for i in range(12)]
    for i, name in enumerate(names):
        seed_job(name,
                 owner.user_id,
                 status='SUCCEEDED',
                 submitted_ago_s=3600 - i * 60)

    page = open_as(owner, '/dashboard/jobs')
    expect(_footer(page, 1, 10, 12),
           'the footer must read "1 – 10 of 12"').to_be_visible()
    expect(_job_link(page, names[11]),
           'page 1 must list my newest job').to_be_visible()
    expect(_job_link(page, names[1]),
           'page 1 must not list my second-oldest job').to_have_count(0)

    page.locator('button:has(path[d="M9 18l6-6-6-6"])').first.click()
    expect(_footer(page, 11, 12, 12),
           'the next page must read "11 – 12 of 12"').to_be_visible()
    expect(page, 'the next page must put page=2 in the URL').to_have_url(
        re.compile(r'[?&]page=2(&|$)'))
    for name in names[:2]:
        expect(_job_link(page, name),
               f'page 2 must list my oldest jobs: {name}').to_be_visible()
    expect(_job_link(page, names[11]),
           'page 2 must not list my newest job').to_have_count(0)

    since = page.recorder.mark()
    page.locator('select:has(option[value="200"])').select_option('30')
    _wait_table_body(page, since, lambda b: b.get('limit') == 30,
                     '30 per page must ask for 30 jobs')
    expect(page, '30 per page must put pageSize=30 in the URL').to_have_url(
        re.compile(r'[?&]pageSize=30(&|$)'))
    expect(_footer(page, 1, 12, 12),
           '30 per page must read 1 – 12 of 12').to_be_visible()
    for name in names:
        expect(_job_link(page, name),
               f'30 per page must list all 12 jobs: {name}').to_be_visible()


@with_twists(
    Twist('dom-job-links-dead',
          'dom',
          expect=r'link must open the job\'s page',
          apply=_swallow_clicks('table a[href*="/jobs/"]')),
    Twist('srv-job-detail-wrong-id',
          'srv',
          expect=r'the job page must show job \d+'),
)
def test_id_and_name_links_open_job_page(make_user, seed_job, open_as, uid,
                                         twist):
    """J-08: the ID and Name links open the job detail page."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='RUNNING')

    page = open_as(owner, '/dashboard/jobs')
    for label in (str(job_id), name):
        expect(_job_link(page, name), 'my job must be listed').to_be_visible()
        _row(page, name).get_by_role('link', name=label, exact=True).click()
        expect(page,
               f'the {label!r} link must open the job\'s page').to_have_url(
                   re.compile(rf'/dashboard/jobs/{job_id}$'))
        expect(page.get_by_role('link', name=f'{job_id} ({name})'),
               f'the job page must show job {job_id} ({name})').to_be_visible()
        page.go_back()
        wait_ready(page)


@with_twists(
    Twist('dom-no-row-download-logs',
          'dom',
          expect=r'my job\'s row must offer Download logs',
          apply=remove_element('button[title="Download logs"]')),
    Twist('srv-jobs-download-logs-wrong-job',
          'srv',
          expect=r'the downloaded zip must hold the job\'s task log'),
    Twist('srv-jobs-download-logs-controller-flipped',
          'srv',
          expect=r'the downloaded zip must hold the job\'s task log'),
)
def test_row_download_logs(make_user, seed_job, job_logs, open_as, uid, twist):
    """J-09: Download logs in a row downloads the job's task logs."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    job_logs.task(job_id, TASK_LOG)
    job_logs.controller(job_id, CONTROLLER_LOG)

    page = open_as(owner, '/dashboard/jobs')
    button = _row(page, name).locator('button[title="Download logs"]')
    expect(button, 'my job\'s row must offer Download logs').to_be_visible()
    since = page.recorder.mark()
    download = _download(page, button.click, 'Download logs in the row')

    body = _wait_body(page, JOBS_DOWNLOAD_LOGS, since, lambda b: True,
                      'Download logs must ask the server for the logs')
    assert (body.get('job_id'), body.get('controller')) == (job_id, False), (
        f'Download logs must ask for job {job_id}\'s task logs, got {body}')
    assert re.match(rf'managed-job-{job_id}-logs-.*\.zip$',
                    download.suggested_filename), (
                        'the zip must be named after the job, got '
                        f'{download.suggested_filename}')
    text = _zip_text(download)
    assert 'e2e hello from head' in text, (
        f'the downloaded zip must hold the job\'s task log, got {text[-500:]}')


@with_twists(
    Twist('srv-jobs-queue-drops-cloud',
          'srv',
          expect=r'the Infra column must link Kubernetes'),
    Twist('dom-infra-link-dead',
          'dom',
          expect=r'the Infra link must open the Infra page',
          apply=_swallow_clicks('table a[href="/dashboard/infra"]')),
)
def test_infra_link_opens_infra_page(make_user, seed_job, open_as, uid, twist):
    """J-10: the Infra column names where the job ran and links to Infra."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    seed_jobs.set_job_details(job_id, cloud='Kubernetes', region='e2e-ctx')

    page = open_as(owner, '/dashboard/jobs')
    row = _row(page, name)
    expect(row, 'my job must be listed').to_be_visible()
    link = row.get_by_role('link', name='Kubernetes', exact=True)
    expect(link, 'the Infra column must link Kubernetes').to_be_visible()
    expect(
        row,
        'the Infra column must name the context').to_contain_text('(e2e-ctx)')
    link.click()
    expect(page, 'the Infra link must open the Infra page').to_have_url(
        re.compile(r'/dashboard/infra/?$'))


@with_twists(
    Twist('srv-jobs-queue-ignores-user-match',
          'srv',
          expect=r'the User chip must hide my own job'),
    Twist('req-clear-keeps-user-filter',
          'req',
          expect=r'clearing the chip must drop user_match',
          apply=_keep_user_filter),
    Twist('dom-user-not-clickable',
          'dom',
          expect=r'must offer Filter by',
          apply=remove_element('button[title^="Filter by"]')),
)
def test_row_user_filter_chip_and_clearing_it(make_user, seed_job, open_as, uid,
                                              twist):
    """S-08 / S-09 and the owner=all, user= URL state: clicking a user in a
    row filters the table to that user; clearing the chip lists everyone
    again."""
    me = make_user('me')
    owner = make_user('owner')
    mine, theirs = f'{uid}-mine', f'{uid}-theirs'
    seed_job(mine, me.user_id, status='RUNNING')
    seed_job(theirs, owner.user_id, status='RUNNING')

    # All Jobs, narrowed to this test's jobs by name so jobs other tests
    # create meanwhile do not push them off the page.
    page = open_as(me, f'/dashboard/jobs?owner=all&name={uid}')
    expect(page.get_by_role('tab', name='All Jobs'),
           'an owner=all link must select All Jobs').to_have_attribute(
               'aria-selected', 'true')
    body = _wait_table_body(page, 0, lambda b: b.get('name_match') == uid,
                            'a name= link must filter the table by name')
    assert not body.get('user_match'), (
        f'an owner=all link must not filter by user, got {body}')
    for name in (mine, theirs):
        expect(_job_link(page, name),
               f'All Jobs must list {name}').to_be_visible()

    user_button = _row(page, theirs).get_by_role('button',
                                                 name=owner.name,
                                                 exact=True)
    expect(user_button, f'the other user\'s row must offer Filter by '
           f'{owner.name}').to_have_attribute('title',
                                              f'Filter by {owner.name}')
    since = page.recorder.mark()
    user_button.click()

    chip = page.locator('div.bg-blue-100').filter(
        has_text=re.compile(rf'User\s*:\s*{owner.name}'))
    expect(chip, f'clicking the user must add a User : {owner.name} chip'
          ).to_be_visible()
    expect(page, 'the User chip must put user= in the URL').to_have_url(
        re.compile(rf'[?&]user={owner.name}(&|$)'))
    _wait_table_body(page, since, lambda b: b.get('user_match') == owner.name,
                     f'the User chip must ask for user_match={owner.name}')
    expect(_job_link(page, theirs),
           'the User chip must list that user\'s job').to_be_visible()
    expect(_job_link(page, mine),
           'the User chip must hide my own job').to_have_count(0)

    since = page.recorder.mark()
    page.reload()
    wait_ready(page)
    expect(chip, 'a user= link must restore the User chip').to_be_visible()
    _wait_table_body(page, since, lambda b: b.get('user_match') == owner.name,
                     'a user= link must filter by that user')
    expect(_job_link(page, mine),
           'the User chip must hide my own job after a reload').to_have_count(0)

    since = page.recorder.mark()
    chip.locator('button[title="Clear filter"]').click()
    expect(chip, 'clearing the chip must remove it').to_have_count(0)
    expect(page,
           'clearing the chip must drop user= from the URL').not_to_have_url(
               re.compile(r'[?&]user='))
    _wait_table_body(page, since, lambda b: not b.get('user_match'),
                     'clearing the chip must drop user_match')
    expect(_job_link(page, mine),
           'clearing the chip must list my own job again').to_be_visible()


# --- job detail page ---------------------------------------------------------

_DETAIL = dict(
    cloud='Kubernetes',
    region='e2e-ctx',
    entrypoint='sky jobs launch -n e2e-train e2e_train.yaml',
    git_commit='0123456789abcdef0123456789abcdef01234567',
    failure_reason='e2e: the user program exited with code 3',
    recovery_count=2,
)


@with_twists(
    Twist('srv-job-detail-wrong-id',
          'srv',
          expect=r'the job page must show job \d+'),
    Twist('res-detail-no-entrypoint',
          'res',
          expect=r'Details must show Entrypoint',
          apply=_edit_detail_jobs(entrypoint=None)),
    Twist('res-detail-stale-status',
          'res',
          expect=r'Details must show Status FAILED',
          apply=_edit_detail_jobs(status='RUNNING')),
)
def test_job_detail_shows_job_details(make_user, seed_job, open_as, uid, twist):
    """JD-01: the Details card."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='FAILED')
    seed_jobs.set_job_details(job_id, **_DETAIL)

    page = open_as(owner, f'/dashboard/jobs/{job_id}')
    body = _wait_body(page, JOBS_QUEUE, 0, _is_detail,
                      'the job page must ask for the job and its tree')
    assert body.get('job_ids') == [
        str(job_id)
    ], (f'the job page must ask for job {job_id}, got {body}')
    expect(page.get_by_role('link', name=f'{job_id} ({name})'),
           f'the job page must show job {job_id} ({name})').to_be_visible()
    details = page.locator('#details-section')
    expected = [
        ('Status', 'FAILED'),
        ('by', re.escape(owner.name)),
        ('Workspace', 'default'),
        ('Recoveries', '2'),
        ('Requested Resources', re.escape('1x[CPU:1]')),
        ('Infra', re.escape('Kubernetes (e2e-ctx)')),
        ('Pool', '-'),
        ('Git Commit', '0123456'),
        ('Details', re.escape('Failure: ' + _DETAIL['failure_reason'])),
        ('Entrypoint', re.escape(_DETAIL['entrypoint'])),
    ]
    for label, value in expected:
        expect(details, f'Details must show {label} {value}'.replace(
            '\\', '')).to_contain_text(re.compile(rf'{label}\s*{value}'))
    expect(details.get_by_role('link', name='Kubernetes'),
           'Details must link the Infra page').to_have_attribute(
               'href', '/dashboard/infra')


@with_twists(
    Twist('srv-jobs-logs-controller-flipped',
          'srv',
          expect=r'the Logs card must show the task\'s log'),
    Twist('dom-view-logs-dead',
          'dom',
          expect=r'View Job Logs must open the job page at tab=logs',
          apply=_swallow_clicks('table button:has(svg.lucide-file-search)')),
)
def test_view_logs_opens_logs_tab_without_ansi(make_user, seed_job, job_logs,
                                               open_as, uid, twist):
    """JD-02 and the tab=logs URL state: the row's logs button opens the job
    page at its Logs card, which shows the task's log without ANSI codes."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    job_logs.task(job_id, TASK_LOG)
    job_logs.controller(job_id, CONTROLLER_LOG)

    page = open_as(owner, '/dashboard/jobs')
    expect(_job_link(page, name), 'my job must be listed').to_be_visible()
    since = page.recorder.mark()
    _row(page, name).locator('button:has(svg.lucide-file-search)').click()

    expect(page,
           'View Job Logs must open the job page at tab=logs').to_have_url(
               re.compile(rf'/dashboard/jobs/{job_id}\?tab=logs$'))
    body = _wait_body(page, JOBS_LOGS, since, lambda b: True,
                      'the Logs card must ask for the logs')
    assert (body.get('job_id'), body.get('controller'),
            body.get('follow')) == (job_id, False, False), (
                f'the Logs card must ask for job {job_id}\'s task log '
                f'without following, got {body}')
    assert body.get('tail', 0) > 0, (
        f'the Logs card must ask for the tail of the log, got {body}')
    pane = _logs_pane(page)
    expect(pane, 'the Logs card must show the task\'s log').to_contain_text(
        'e2e hello from head')
    expect(pane, 'the Logs card must show the task\'s log, not the '
           'controller\'s').not_to_contain_text('e2e controller line one')
    text = pane.inner_text()
    assert '\x1b' not in text and '[32m' not in text, (
        f'the Logs card must strip ANSI codes, got {text!r}')


@with_twists(
    # Flipped both ways: the Logs card shows the controller's log first.
    Twist('srv-jobs-logs-controller-flipped',
          'srv',
          expect=r'the Logs card must show the task\'s log'),
    Twist('src-jobs-logs-ignore-controller',
          'src',
          expect=r'Controller Logs must show the controller\'s log',
          patch='twist_patches/jobs-logs-ignore-controller.patch'),
    Twist('dom-controller-logs-toggle-dead',
          'dom',
          expect=r'Controller Logs must ask for the controller\'s log',
          apply=_swallow_clicks('#controller-logs-section button')),
)
def test_controller_logs(make_user, seed_job, job_logs, open_as, uid, twist):
    """JD-03: expanding Controller Logs shows the controller's log."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    job_logs.task(job_id, TASK_LOG)
    job_logs.controller(job_id, CONTROLLER_LOG)

    page = open_as(owner, f'/dashboard/jobs/{job_id}')
    expect(_logs_pane(page),
           'the Logs card must show the task\'s log').to_contain_text(
               'e2e hello from head')
    since = page.recorder.mark()
    page.get_by_role('button', name=re.compile(r'^Controller Logs')).click()

    _wait_body(
        page, JOBS_LOGS, since,
        lambda b: b.get('controller') is True and b.get('job_id') == job_id,
        'Controller Logs must ask for the controller\'s log')
    pane = _logs_pane(page, '#controller-logs-section')
    expect(pane,
           'Controller Logs must show the controller\'s log').to_contain_text(
               'e2e controller line one')
    expect(
        pane, 'Controller Logs must show the controller\'s log, not the '
        'task\'s').not_to_contain_text('e2e hello from head')


@with_twists(
    Twist('res-logs-lose-node-prefix',
          'res',
          expect=r'the Node picker must offer Worker1',
          apply=_strip_from_logs('(worker1, rank=1, pid=102) ')),
    Twist('dom-no-node-picker',
          'dom',
          expect=r'the Node picker must be offered',
          apply=remove_element('[aria-label="Node"]')),
)
def test_node_picker_filters_log_lines(make_user, seed_job, job_logs, open_as,
                                       uid, twist):
    """JD-04: the Node picker shows only the chosen node's lines."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    job_logs.task(job_id, TASK_LOG)

    page = open_as(owner, f'/dashboard/jobs/{job_id}')
    pane = _logs_pane(page)
    expect(pane, 'the Logs card must show the task\'s log').to_contain_text(
        'e2e hello from worker1')
    picker = page.get_by_role('combobox', name='Node')
    expect(picker, 'the Node picker must be offered').to_be_visible()
    picker.click()
    for option in ('All Nodes', 'Head', 'Worker1'):
        expect(page.get_by_role('option', name=option, exact=True),
               f'the Node picker must offer {option}').to_be_visible()
    page.get_by_role('option', name='Worker1', exact=True).click()
    expect(pane, 'Worker1 must show worker1\'s lines').to_contain_text(
        'e2e hello from worker1')
    expect(pane, 'Worker1 must hide the head\'s lines').not_to_contain_text(
        'e2e hello from head')

    picker.click()
    page.get_by_role('option', name='Head', exact=True).click()
    expect(pane, 'Head must show the head\'s lines').to_contain_text(
        'e2e hello from head')
    expect(pane, 'Head must hide worker1\'s lines').not_to_contain_text(
        'e2e hello from worker1')


@with_twists(
    Twist('srv-jobs-download-logs-controller-flipped',
          'srv',
          expect=r'the Logs card\'s zip must hold the task\'s log'),
    Twist(
        'dom-logs-download-dead',
        'dom',
        expect=r'the Logs card\'s download button must download a zip',
        apply=_swallow_clicks('#logs-section button:has(svg.lucide-download)')),
)
def test_detail_download_task_and_controller_logs(make_user, seed_job, job_logs,
                                                  open_as, uid, twist):
    """JD-05: the Logs and Controller Logs cards download their logs."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    job_logs.task(job_id, TASK_LOG)
    job_logs.controller(job_id, CONTROLLER_LOG)

    page = open_as(owner, f'/dashboard/jobs/{job_id}')
    expect(_logs_pane(page),
           'the Logs card must show the task\'s log').to_contain_text(
               'e2e hello from head')

    since = page.recorder.mark()
    download = _download(
        page,
        page.locator('#logs-section button:has(svg.lucide-download)').click,
        'the Logs card\'s download button')
    body = _wait_body(page, JOBS_DOWNLOAD_LOGS, since, lambda b: True,
                      'the Logs card must ask the server for the logs')
    assert (body.get('job_id'), body.get('controller')) == (job_id, False), (
        f'the Logs card must download job {job_id}\'s task logs, got {body}')
    assert re.match(rf'managed-job-{job_id}-logs-.*\.zip$',
                    download.suggested_filename), (
                        'the Logs card\'s zip must be named after the job, got '
                        f'{download.suggested_filename}')
    text = _zip_text(download)
    assert 'e2e hello from head' in text, (
        f'the Logs card\'s zip must hold the task\'s log, got {text[-500:]}')

    page.get_by_role('button', name=re.compile(r'^Controller Logs')).click()
    since = page.recorder.mark()
    download = _download(
        page,
        page.locator(
            '#controller-logs-section button:has(svg.lucide-download)').click,
        'the Controller Logs card\'s download button')
    body = _wait_body(page, JOBS_DOWNLOAD_LOGS, since, lambda b: True,
                      'Controller Logs must ask the server for the logs')
    assert (body.get('job_id'), body.get('controller')) == (job_id, True), (
        f'Controller Logs must download job {job_id}\'s controller logs, '
        f'got {body}')
    assert re.match(rf'managed-job-{job_id}-controller-logs-.*\.zip$',
                    download.suggested_filename), (
                        'the controller zip must be named after the job, got '
                        f'{download.suggested_filename}')
    text = _zip_text(download)
    assert 'e2e controller line one' in text, (
        f'the controller zip must hold the controller\'s log, got {text[-500:]}'
    )


def _wait_clipboard(page: Page, expected: str, message: str) -> None:
    deadline = time.time() + 10
    got = None
    while time.time() < deadline:
        got = page.evaluate('navigator.clipboard.readText()')
        if expected in got:
            return
        page.wait_for_timeout(200)
    raise AssertionError(f'{message}, got {got!r}')


@with_twists(
    Twist('res-detail-no-yaml',
          'res',
          expect=r'SkyPilot YAML must be offered',
          apply=_edit_detail_jobs(user_yaml=None)),
    Twist('dom-copy-command-dead',
          'dom',
          expect=r'Copy command must put the entrypoint on the clipboard',
          apply=_swallow_clicks('button[title="Copy command"]')),
)
def test_yaml_and_copy_buttons(make_user, seed_job, open_as, uid, twist):
    """JD-06: SkyPilot YAML expands, Copy YAML and Copy command copy."""
    owner = make_user('owner')
    name = f'{uid}-train'
    job_id = seed_job(name, owner.user_id, status='SUCCEEDED')
    yaml_text = f'name: {name}\nrun: echo e2e-yaml-{uid}\n'
    seed_jobs.set_job_details(job_id,
                              entrypoint=_DETAIL['entrypoint'],
                              user_yaml=yaml_text)

    page = open_as(owner, f'/dashboard/jobs/{job_id}')
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    toggle = page.get_by_role('button', name='SkyPilot YAML')
    expect(toggle, 'SkyPilot YAML must be offered').to_be_visible()
    toggle.click()
    expect(page.locator('#details-section'),
           'SkyPilot YAML must show the job\'s YAML').to_contain_text(
               f'echo e2e-yaml-{uid}')

    page.locator('#details-section button[title="Copy YAML"]').click()
    _wait_clipboard(page, f'echo e2e-yaml-{uid}',
                    'Copy YAML must put the YAML on the clipboard')
    page.locator('#details-section button[title="Copy command"]').click()
    _wait_clipboard(page, _DETAIL['entrypoint'],
                    'Copy command must put the entrypoint on the clipboard')
