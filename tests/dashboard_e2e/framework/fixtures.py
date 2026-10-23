"""Pytest plugin for the dashboard e2e suite: fixtures, twist verdicts and the
exclusive-test rules.

A suite's conftest loads it with ``pytest_plugins = ['framework.fixtures']``.
The suite runs against one live API server started by ``run_server.sh``; its
exports (E2E_BASE_URL, SKY_RUNTIME_DIR, admin credentials) must be in the
environment.

Concurrency model:

- A normal test creates its own users (``make_user``) and rows (``seed_job``),
  asserts only on those, and runs in parallel with every other normal test.
- A test that asserts global state (totals, what lands on the first page) is
  marked ``exclusive``. ``run_tests.sh`` runs those one at a time after the
  parallel phase; each refuses to start if rows other tests created are still
  in the DB, and fails if it is run under ``-n``.
"""
import dataclasses
import importlib
import json
import os
import re
from typing import Any, Callable, Dict, Iterator, List, Optional
import uuid

from framework import seed
from framework.twists import Twist
from playwright.sync_api import Browser
from playwright.sync_api import BrowserContext
from playwright.sync_api import expect
from playwright.sync_api import Page
from playwright.sync_api import Request
import pytest
import requests

# Several browsers share one server, so a table can take a few seconds to
# re-render after a click.
expect.set_options(timeout=15_000)

# Requests from 127.0.0.1 skip auth (sky/server/auth/loopback.py) unless a
# proxy header is present. Send one everywhere so RBAC is really enforced.
PROXY_HEADERS = {'X-Forwarded-For': '203.0.113.7'}
TWIST_HEADER = 'X-E2E-Twist'
TWIST_REPORT_ENV = 'E2E_TWIST_REPORT'
SKIP_TOUR = """
  try {
    localStorage.setItem('skypilot-dashboard-first-visit', 'true');
    localStorage.setItem('skypilot-dashboard-tour-completed', 'true');
  } catch (e) {}
"""


@dataclasses.dataclass
class Identity:
    name: str
    role: str
    user_id: str
    token: str
    token_id: str


# --- server and admin --------------------------------------------------------


@pytest.fixture(scope='session')
def e2e_base_url() -> str:
    """Not named base_url: pytest-base-url (pulled in by pytest-playwright)
    owns that fixture."""
    url = os.environ.get('E2E_BASE_URL')
    if not url:
        pytest.exit('E2E_BASE_URL is not set: start the server with '
                    'framework/run_server.sh first')
    return url.rstrip('/')


@pytest.fixture(scope='session')
def admin_api(e2e_base_url: str) -> requests.Session:
    s = requests.Session()
    s.auth = (os.environ['E2E_ADMIN_USER'], os.environ['E2E_ADMIN_PW'])
    s.headers.update(PROXY_HEADERS)
    s.base_url = e2e_base_url  # type: ignore[attr-defined]
    return s


@pytest.fixture
def uid() -> str:
    """Short unique prefix for everything this test creates."""
    return 't' + uuid.uuid4().hex[:6]


@pytest.fixture
def make_user(admin_api: requests.Session,
              uid: str) -> Iterator[Callable[..., Identity]]:
    """Create a service-account user with a role; removed afterwards."""
    created: List[Identity] = []

    def make(label: str, role: str = 'user') -> Identity:
        name = f'{uid}-{label}'
        r = admin_api.post(
            f'{admin_api.base_url}/users/service-account-tokens',  # type: ignore[attr-defined]
            json={
                'token_name': name,
                'expires_in_days': 1,
                'role': role
            },
            timeout=60)
        assert r.status_code == 200, (
            f'creating service account {name}: {r.status_code} {r.text}')
        body = r.json()
        ident = Identity(name, role, body['service_account_user_id'],
                         body['token'], body['token_id'])
        created.append(ident)
        return ident

    yield make
    for ident in created:
        admin_api.post(
            f'{admin_api.base_url}/users/service-account-tokens/delete',  # type: ignore[attr-defined]
            json={'token_id': ident.token_id},
            timeout=60)


@pytest.fixture
def seed_job() -> Iterator[Callable[..., int]]:
    """Insert managed jobs for this test and delete them afterwards."""
    job_ids: List[int] = []

    def make(*args, **kwargs) -> int:
        job_id = seed.managed_job(*args, **kwargs)
        job_ids.append(job_id)
        return job_id

    yield make
    for job_id in job_ids:
        seed.delete_managed_job(job_id)


@pytest.fixture
def seed_cluster() -> Iterator[Callable[..., str]]:
    """Insert clusters for this test and delete them afterwards."""
    names: List[str] = []

    def make(*args, **kwargs) -> str:
        name = seed.cluster(*args, **kwargs)
        names.append(name)
        return name

    yield make
    for name in names:
        seed.delete_cluster(name)


# --- browser -----------------------------------------------------------------


class Recorder:
    """Records the requests one page sends."""

    def __init__(self, page: Page):
        self.requests: List[Request] = []
        page.on('request', lambda r: self.requests.append(r))  # pylint: disable=unnecessary-lambda

    def mark(self) -> int:
        return len(self.requests)

    def posts(self, path_suffix: str, since: int = 0) -> List[Dict[str, Any]]:
        """JSON bodies of POSTs to ``*path_suffix`` since ``mark()``."""
        out = []
        for r in self.requests[since:]:
            if r.method == 'POST' and r.url.split('?')[0].endswith(path_suffix):
                out.append(json.loads(r.post_data or '{}'))
        return out


@pytest.fixture
def twist() -> Optional[Twist]:
    """Overridden by @with_twists; None means a plain baseline run."""
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # pylint: disable=unused-argument
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)


@pytest.fixture
def open_as(browser: Browser, e2e_base_url: str, twist: Optional[Twist],
            request: pytest.FixtureRequest) -> Iterator[Callable[..., Page]]:
    """Open the dashboard as an identity. The test's twist, if any, applies to
    every context it opens. A failing test leaves a trace and a screenshot of
    every page under test-results/."""
    contexts: List[BrowserContext] = []

    def open_page(ident: Identity, path: str = '/dashboard/') -> Page:
        headers = {'Authorization': f'Bearer {ident.token}', **PROXY_HEADERS}
        if twist is not None and twist.layer == 'srv':
            headers[TWIST_HEADER] = twist.id
        ctx = browser.new_context(base_url=e2e_base_url,
                                  extra_http_headers=headers,
                                  viewport={
                                      'width': 1440,
                                      'height': 900
                                  })
        ctx.add_init_script(SKIP_TOUR)
        if twist is not None and twist.apply is not None:
            twist.apply(ctx)
        ctx.tracing.start(screenshots=True, snapshots=True)
        contexts.append(ctx)
        page = ctx.new_page()
        page.recorder = Recorder(page)  # type: ignore[attr-defined]
        page.goto(path)
        wait_ready(page)
        return page

    yield open_page
    rep = getattr(request.node, 'rep_call', None)
    failed = rep is not None and rep.failed
    keep_all = os.environ.get('E2E_SCREENSHOT_ALL') == '1'
    # E2E_RESULTS_DIR lets several runs that share one checkout keep their
    # traces and screenshots apart.
    results = os.environ.get('E2E_RESULTS_DIR') or os.path.join(
        str(request.config.rootpath), 'test-results')
    out_dir = os.path.join(results, re.sub(r'[^\w.-]+', '_',
                                           request.node.nodeid))
    for i, ctx in enumerate(contexts):
        if failed or keep_all:
            os.makedirs(out_dir, exist_ok=True)
            for j, page in enumerate(ctx.pages):
                page.screenshot(path=os.path.join(out_dir,
                                                  f'ctx{i}-page{j}.png'),
                                full_page=True)
        if failed:
            ctx.tracing.stop(path=os.path.join(out_dir, f'ctx{i}-trace.zip'))
        else:
            ctx.tracing.stop()
        ctx.close()


def wait_ready(page: Page, timeout_ms: int = 60_000) -> None:
    """The dashboard renders nothing until its plugins load; wait for the
    navigation, then for the page's own loading state to clear."""
    # The OSS top bar and the plugin sidebar both link to Clusters.
    page.locator('a[href="/dashboard/clusters"]').first.wait_for(
        timeout=timeout_ms)
    loading = page.get_by_text(re.compile(r'^Loading'))
    try:
        loading.first.wait_for(state='hidden', timeout=timeout_ms)
    except Exception:  # pylint: disable=broad-except
        pass


# --- twist verdicts ----------------------------------------------------------


class TwistNotDetected(AssertionError):
    pass


class TwistWrongReason(AssertionError):
    pass


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function):
    """A twist run passes only if the test body fails, and fails with the
    message the twist names."""
    twist_: Optional[Twist] = pyfuncitem.funcargs.get('twist')
    if twist_ is None:
        return None
    args = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames  # pylint: disable=protected-access
    }
    try:
        pyfuncitem.obj(**args)
    except Exception as e:  # pylint: disable=broad-except
        message = str(e)
        if not re.search(twist_.expect, message, re.S):
            raise TwistWrongReason(
                f'twist {twist_.id} ({twist_.layer}) failed for the wrong '
                f'reason.\nexpected /{twist_.expect}/\ngot: {message[:2000]}'
            ) from e
        pyfuncitem.user_properties.append(
            ('twist_caught', message.splitlines()[0][:300]))
        return True
    raise TwistNotDetected(f'twist {twist_.id} ({twist_.layer}) was applied '
                           'but the test still passed')


def pytest_configure(config) -> None:
    # Import every seed_*.py in the suite so each registers its leftover
    # checks; an exclusive test must wait for every area's rows, not only the
    # areas its own file happens to import.
    for path in sorted(config.rootpath.glob('seed_*.py')):
        importlib.import_module(path.stem)
    config.addinivalue_line(
        'markers',
        'exclusive: asserts global state (totals, page composition), so it '
        'runs alone after the parallel tests: pytest -m exclusive')


@pytest.fixture(autouse=True)
def _exclusive_starts_clean(request: pytest.FixtureRequest) -> None:
    """An exclusive test asserts exact totals, so nothing another test created
    may still be in the DB when it starts."""
    if request.node.get_closest_marker('exclusive') is None:
        return
    if os.environ.get('PYTEST_XDIST_WORKER'):
        pytest.fail('exclusive test ran under -n, sharing the server with '
                    'other tests; run exclusive tests on their own: pytest -m '
                    'exclusive (run_tests.sh does this)')
    leftovers = seed.leftover_test_rows()
    assert not leftovers, ('exclusive test started with rows other tests left '
                           f'behind: {leftovers[:10]}')


def pytest_collection_modifyitems(config, items: List[pytest.Item]) -> None:
    del config
    for item in items:
        callspec = getattr(item, 'callspec', None)
        if callspec is None or 'twist' not in callspec.params:
            raise pytest.UsageError(
                f'{item.nodeid}: every dashboard e2e test needs '
                '@with_twists(...)')


def pytest_runtest_logreport(report) -> None:
    """Append each run's verdict to $E2E_TWIST_REPORT (JSON lines)."""
    path = os.environ.get(TWIST_REPORT_ENV)
    if not path or report.when != 'call':
        return
    caught = dict(report.user_properties).get('twist_caught')
    with open(path, 'a', encoding='utf-8') as f:
        f.write(
            json.dumps({
                'test': report.nodeid,
                'outcome': report.outcome,
                'caught': caught,
            }) + '\n')


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    del exitstatus, config
    rows = []
    for rep in terminalreporter.stats.get('passed', []):
        for k, v in rep.user_properties:
            if k == 'twist_caught':
                rows.append(f'  CAUGHT  {rep.nodeid}\n          -> {v}')
    if rows:
        terminalreporter.write_sep('=', 'twists caught for the expected reason')
        terminalreporter.write_line('\n'.join(rows))
