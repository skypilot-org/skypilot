"""Managed Jobs list scope: "My Jobs" vs "All Jobs".

The dashboard scopes "My Jobs" by sending ``user_match=<me>``; "All Jobs"
drops it. A user who owns no jobs gets a hint with the total and a "View all
jobs" button. Regressions here leave such a user staring at an empty table:
the button does nothing, or the user filter survives the switch.

Tests asserting global numbers ("N jobs in total", "1 – 10 of N", status
counts) are ``exclusive``: they run alone and read expected numbers from the
DB. The rest assert only on rows they created and run in parallel.
"""
import re
import time
from typing import Any, Dict, List

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
from playwright.sync_api import Route
import pytest

JOBS_QUEUE = '/jobs/queue/v2'


def _is_jobs_page(body: Dict[str, Any]) -> bool:
    """A jobs-table request: paged, and not the one-row total-count probe."""
    return 'page' in body and body.get('limit') != 1


def _table_requests(page, since: int = 0) -> List[Dict[str, Any]]:
    return [
        b for b in page.recorder.posts(JOBS_QUEUE, since) if _is_jobs_page(b)
    ]


def _wait_table_request(page,
                        since: int,
                        timeout_s: float = 15) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reqs = _table_requests(page, since)
        if reqs:
            return reqs[-1]
        page.wait_for_timeout(200)
    raise AssertionError('the jobs table sent no list request')


def _keep_user_filter(context: BrowserContext) -> None:
    """req twist: once the table has asked for "my" jobs, keep that user
    filter on every later table request, so "All Jobs" stays scoped to me."""
    seen: Dict[str, str] = {}

    def handler(route: Route) -> None:
        import json  # pylint: disable=import-outside-toplevel
        body = json.loads(route.request.post_data or '{}')
        if _is_jobs_page(body):
            if body.get('user_match'):
                seen['user'] = body['user_match']
            elif 'user' in seen:
                body['user_match'] = seen['user']
                route.continue_(post_data=json.dumps(body))
                return
        route.continue_()

    context.route(f'**{JOBS_QUEUE}', handler)


def _total_off_by_one(context: BrowserContext) -> None:
    """res twist: the total-count probe answers one more job than exists.

    Job queries are async: the POST returns a request id and the result comes
    from /api/get. Remember the probe's request id, then bump the total in
    that result."""
    import json  # pylint: disable=import-outside-toplevel
    probe_ids = set()

    def on_queue(route: Route) -> None:
        resp = route.fetch()
        body = json.loads(route.request.post_data or '{}')
        if body.get('limit') == 1:
            probe_ids.add(resp.headers.get('x-skypilot-request-id'))
        route.fulfill(response=resp)

    def on_get(route: Route) -> None:
        resp = route.fetch()
        rid = route.request.url.split('request_id=')[-1].split('&')[0]
        if rid not in probe_ids or resp.status != 200:
            route.fulfill(response=resp)
            return
        data = resp.json()
        value = json.loads(data['return_value'])
        value['total_no_filter'] = value.get('total_no_filter', 0) + 1
        value['total'] = value.get('total', 0) + 1
        data['return_value'] = json.dumps(value)
        route.fulfill(response=resp, json=data)

    context.route(f'**{JOBS_QUEUE}', on_queue)
    context.route('**/api/get?*', on_get)


@with_twists(
    Twist('req-my-jobs-not-scoped',
          'req',
          expect=r'My Jobs must filter the table to the viewer',
          apply=rewrite_request(
              JOBS_QUEUE,
              lambda b: {k: v for k, v in b.items() if k != 'user_match'}
              if _is_jobs_page(b) else b)),
    Twist('dom-no-view-all-button',
          'dom',
          expect=r'"View all jobs" must be offered',
          apply=remove_button('View all jobs')),
    Twist('res-total-off-by-one',
          'res',
          expect=r'the hint must say how many jobs exist in total',
          apply=_total_off_by_one),
)
@pytest.mark.exclusive
def test_zero_job_viewer_is_pointed_to_all_jobs(make_user, seed_job, open_as,
                                                uid, twist):
    owner = make_user('owner')
    viewer = make_user('viewer')
    seed_job(f'{uid}-train', owner.user_id, status='RUNNING')

    page = open_as(viewer, '/dashboard/jobs')

    body = _wait_table_request(page, 0)
    assert body.get('user_match') == viewer.name, (
        f'My Jobs must filter the table to the viewer ({viewer.name}), '
        f'got {body}')
    expect(
        page.get_by_text("You haven't submitted any managed jobs yet."),
        'a user with no jobs must be told the table is scoped to them',
    ).to_be_visible()
    total = seed.count_managed_jobs()
    noun = 'job' if total == 1 else 'jobs'
    expect(
        page.get_by_text(re.compile(rf'(^|\D){total:,} {noun} in total')),
        f'the hint must say how many jobs exist in total ({total})',
    ).to_be_visible()
    expect(
        page.get_by_role('button', name='View all jobs'),
        '"View all jobs" must be offered to a user with no jobs',
    ).to_be_visible()


@with_twists(
    Twist('req-view-all-keeps-user-filter',
          'req',
          expect=r'View all jobs must not keep a user filter',
          apply=_keep_user_filter),
    Twist('dom-view-all-does-nothing',
          'dom',
          expect=r'View all jobs must switch the URL to owner=all',
          apply=swallow_click('View all jobs')),
    Twist('srv-jobs-queue-ignores-all-users',
          'srv',
          expect=r"another user's job .* must be listed under All Jobs"),
    # A backend that ignores all_users shows either symptom first, depending
    # on which query returns first: the hint loses its total (no button), or
    # All Jobs comes back without the other user's rows.
    Twist('src-jobs-queue-ignores-all-users',
          'src',
          expect=(r"another user's job .* must be listed under All Jobs"
                  r'|"View all jobs" must be offered'),
          patch='twist_patches/jobs-queue-ignores-all-users.patch'),
)
@pytest.mark.exclusive
def test_view_all_jobs_shows_other_users_jobs(make_user, seed_job, open_as, uid,
                                              twist):
    owner = make_user('owner')
    viewer = make_user('viewer')
    names = [f'{uid}-train', f'{uid}-eval']
    seed_job(names[0], owner.user_id, status='RUNNING')
    seed_job(names[1], owner.user_id, status='SUCCEEDED')

    page = open_as(viewer, '/dashboard/jobs')
    view_all = page.get_by_role('button', name='View all jobs')
    expect(view_all, '"View all jobs" must be offered to a user with no jobs'
          ).to_be_visible()
    since = page.recorder.mark()
    view_all.click()

    expect(page, 'View all jobs must switch the URL to owner=all').to_have_url(
        re.compile(r'[?&]owner=all'))
    body = _wait_table_request(page, since)
    assert not body.get('user_match'), (
        f'View all jobs must not keep a user filter, got {body}')
    # The default page holds 10 rows, newest first; this test's jobs are the
    # newest because nothing else runs beside an exclusive test.
    for name in names:
        expect(
            page.get_by_role('link', name=name, exact=True),
            f"another user's job {name} must be listed under All Jobs",
        ).to_be_visible()
    total = seed.count_managed_jobs()
    expect(
        page.get_by_text(
            re.compile(rf'1\s*–\s*{min(10, total)}\s+of\s+{total}\b')),
        f'All Jobs footer must read "1 – {min(10, total)} of {total}"',
    ).to_be_visible()
    running = seed.count_managed_jobs_by_status().get('RUNNING', 0)
    expect(
        page.get_by_role('button', name=re.compile(rf'^RUNNING\s+{running}$')),
        f'All Jobs must count {running} RUNNING jobs across all users',
    ).to_be_visible()


@with_twists(
    Twist('dom-refetch-per-keystroke',
          'dom',
          expect=r'must not refetch while typing',
          apply=run_script("""
            document.addEventListener('input', (e) => {
              if (e.target.placeholder === 'Filter jobs') {
                const r = [...document.querySelectorAll('button')]
                  .find(b => b.innerText.trim() === 'Refresh');
                if (r) r.click();
              }
            }, true);
          """)),
    Twist('dom-no-filter-box',
          'dom',
          expect=r'"Filter jobs" box must be present',
          apply=remove_element('input[placeholder="Filter jobs"]')),
)
def test_typing_in_job_filter_does_not_refetch(make_user, seed_job, open_as,
                                               uid, twist):
    owner = make_user('owner')
    seed_job(f'{uid}-train-a', owner.user_id, status='RUNNING')
    seed_job(f'{uid}-eval-b', owner.user_id, status='RUNNING')

    page = open_as(owner, '/dashboard/jobs')
    box = page.get_by_placeholder('Filter jobs')
    expect(box, '"Filter jobs" box must be present').to_be_visible()
    expect(page.get_by_role('link', name=f'{uid}-train-a', exact=True),
           'seeded job must be listed').to_be_visible()

    since = page.recorder.mark()
    # 450 ms per key is longer than a 350 ms debounce that would refetch
    # mid-word.
    box.press_sequentially(f'{uid}-train', delay=450)
    page.wait_for_timeout(1500)

    refetches = page.recorder.posts(JOBS_QUEUE, since)
    assert not refetches, ('the jobs table must not refetch while typing, '
                           f'got {len(refetches)} requests')
    expect(page.get_by_text('No active jobs'),
           'the table must not flash empty while typing').to_have_count(0)
