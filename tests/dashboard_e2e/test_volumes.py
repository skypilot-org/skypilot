"""Volumes list and detail pages.

Volumes are seeded into the server's DB (seed_volumes.py) on a Kubernetes
context that does not exist, so the list and detail pages read real records
through GET /volumes, and deleting one fails on the cloud side the way it does
for a cluster that is gone. That is what drives the "Force remove" path.

The volume list shows every volume of the workspaces the viewer can read, so
other tests' volumes may be on screen too. Each test filters to its own
``uid`` prefix and asserts only on its own rows, so all of them run in
parallel.
"""
import json
import re
import time
from typing import Any, Dict, List
import urllib.parse

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
import seed_volumes
from seed_volumes import seed_volume  # pylint: disable=unused-import

VOLUMES = '/volumes'
DELETE = '/volumes/delete'


def _path(url: str) -> str:
    return urllib.parse.urlparse(url).path


def _wait_requests(page: Page,
                   method: str,
                   path: str,
                   since: int = 0,
                   what: str = '',
                   timeout_s: float = 20) -> List[Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reqs = [
            r for r in page.recorder.requests[since:]
            if r.method == method and _path(r.url).endswith(path)
        ]
        if reqs:
            return reqs
        page.wait_for_timeout(200)
    raise AssertionError(what or f'the page sent no {method} {path}')


def _row(page: Page, name: str):
    return page.locator('tbody tr').filter(
        has=page.get_by_role('link', name=name, exact=True)).first


def _cell(page: Page, row, header: str):
    """The cell of ``row`` under the column titled ``header``."""
    headers = [
        re.sub(r'[\s↑↓]+$', '', h.strip())
        for h in page.locator('thead th').all_inner_texts()
    ]
    assert header in headers, f'the volumes table must have a {header} column'
    return row.locator('td').nth(headers.index(header))


def _detail(page: Page, label: str):
    """The value under a label on the volume detail card."""
    return page.locator(
        f'xpath=//div[normalize-space()="{label}"]/following-sibling::div[1]')


def _wait_volume_gone(name: str, timeout_s: float = 20) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not seed_volumes.volume_exists(name):
            return True
        time.sleep(0.3)
    return False


# --- twist helpers -----------------------------------------------------------


def _rewrite_volume_list(fn):
    """res twist: rewrite the records GET /volumes returns. The list is an
    async request: remember its request id, then rewrite that /api/get."""

    def apply(context: BrowserContext) -> None:
        ids = set()

        def on_list(route: Route) -> None:
            if route.request.method != 'GET':
                route.continue_()
                return
            resp = route.fetch()
            ids.add(resp.headers.get('x-skypilot-request-id'))
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in ids:
                route.continue_()
                return
            resp = route.fetch()
            data = resp.json()
            records = json.loads(data.get('return_value') or '[]')
            data['return_value'] = json.dumps(fn(records))
            route.fulfill(response=resp, json=data)

        context.route(f'**{VOLUMES}', on_list)
        context.route('**/api/get?*', on_get)

    return apply


def _drop_region(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**r, 'region': None} for r in records]


def _drop_test_volumes(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in records if not re.match(r't[0-9a-f]{6}-', r['name'])]


def _drop_config(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**r, 'config': {}} for r in records]


_SWALLOW_FILTER_ENTER = run_script("""
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.placeholder === 'Filter volumes') {
      e.stopImmediatePropagation();
      e.preventDefault();
    }
  }, true);
""")

# --- tests -------------------------------------------------------------------


@with_twists(
    Twist('src-infra_volumes-volume-list-drops-owner',
          'src',
          expect=r'the User column must show the owner',
          patch='twist_patches/infra_volumes-volume-list-drops-owner.patch'),
    Twist('res-volumes-infra-loses-context',
          'res',
          expect=r'the Infra column must show the context',
          apply=_rewrite_volume_list(_drop_region)),
    Twist('dom-volumes-no-delete-button',
          'dom',
          expect=r'each volume row must offer Delete volume',
          apply=remove_element('button[title="Delete volume"]')),
    Twist('dom-volumes-filter-ignores-enter',
          'dom',
          expect=r'the name filter must be written to the URL',
          apply=_SWALLOW_FILTER_ENTER),
)
def test_volume_list_columns_and_name_filter(make_user, seed_volume, open_as,
                                             uid, twist):
    owner = make_user('owner')
    data = seed_volume(f'{uid}-data',
                       owner.user_id,
                       status='READY',
                       context='e2e-ctx',
                       size='10',
                       usedby_clusters=[f'{uid}-train'])
    ckpt = seed_volume(
        f'{uid}-ckpt',
        owner.user_id,
        status='NOT_READY',
        error_message='waiting for first consumer to be created before binding')

    page = open_as(owner, '/dashboard/volumes')
    _wait_requests(page,
                   'GET',
                   VOLUMES,
                   what='the Volumes page must list volumes (GET /volumes)')
    box = page.get_by_placeholder('Filter volumes')
    expect(box, '"Filter volumes" box must be present').to_be_visible()
    box.fill(f'{uid}-')
    box.press('Enter')
    expect(page, 'the name filter must be written to the URL').to_have_url(
        re.compile(rf'[?&]name={uid}-'))

    row = _row(page, data)
    expect(row, f'volume {data} must be listed').to_be_visible()
    expect(_row(page, ckpt), f'volume {ckpt} must be listed').to_be_visible()
    expect(row.get_by_role('link', name=data, exact=True),
           'the volume name must link to its detail page').to_have_attribute(
               'href', f'/dashboard/volumes/{data}')
    expect(_cell(page, row, 'Infra'),
           'the Infra column must show the context the volume lives on'
          ).to_have_text('kubernetes/e2e-ctx')
    expect(_cell(page, row, 'Status'),
           'the Status column must show READY').to_have_text('READY')
    expect(_cell(page, row, 'Size'),
           'the Size column must show the size in Gi').to_have_text('10Gi')
    expect(_cell(page, row, 'User'),
           f'the User column must show the owner ({owner.name})').to_have_text(
               owner.name)
    expect(_cell(page, row, 'Type'),
           'the Type column must show k8s-pvc').to_have_text('k8s-pvc')
    expect(
        _cell(page, row, 'Used By').get_by_role('link',
                                                name=f'{uid}-train',
                                                exact=True),
        'Used By must link the cluster that mounts the volume').to_be_visible()
    expect(row.locator('button[title="Delete volume"]'),
           'each volume row must offer Delete volume').to_be_visible()

    ckpt_row = _row(page, ckpt)
    expect(_cell(page, ckpt_row, 'Status'),
           'a pending volume must show NOT_READY').to_have_text('NOT_READY')
    expect(_cell(page, ckpt_row, 'Details'),
           'Details must show why the volume is not ready').to_contain_text(
               'waiting for first consumer')

    box.fill(ckpt)
    box.press('Enter')
    expect(page, 'the name filter must be written to the URL').to_have_url(
        re.compile(rf'[?&]name={ckpt}'))
    expect(page.get_by_role('link', name=data, exact=True),
           f'filtering by name {ckpt} must hide {data}').to_have_count(0)
    expect(_row(page, ckpt),
           f'filtering by name must keep {ckpt}').to_be_visible()


@with_twists(
    Twist('srv-infra_volumes-delete-drops-purge',
          'srv',
          expect=r'Force remove must remove the volume'),
    Twist('req-volumes-delete-wrong-name',
          'req',
          expect=r'Delete must name the volume',
          apply=rewrite_request(
              DELETE, lambda b: {
                  **b, 'names': [n + '-x' for n in b.get('names', [])]
              })),
    Twist('dom-volumes-confirm-does-nothing',
          'dom',
          expect=r'confirming Delete must send POST /volumes/delete',
          apply=swallow_click('Delete')),
)
def test_delete_volume_offers_force_remove_when_cloud_delete_fails(
        make_user, seed_volume, open_as, uid, twist):
    owner = make_user('owner')
    name = seed_volume(f'{uid}-gone', owner.user_id, status='READY')

    page = open_as(owner, f'/dashboard/volumes?name={name}')
    row = _row(page, name)
    expect(row, f'volume {name} must be listed').to_be_visible()
    row.locator('button[title="Delete volume"]').click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_role('heading', name='Delete Volume'),
           'Delete volume must ask for confirmation').to_be_visible()
    expect(dialog, 'the confirmation must name the volume').to_contain_text(
        f'Are you sure you want to delete volume "{name}"?')

    since = page.recorder.mark()
    dialog.get_by_role('button', name='Delete', exact=True).click()
    bodies = [
        json.loads(r.post_data or '{}') for r in _wait_requests(
            page,
            'POST',
            DELETE,
            since,
            what='confirming Delete must send POST /volumes/delete')
    ]
    assert bodies[-1].get('names') == [name] and not bodies[-1].get('purge'), (
        f'Delete must name the volume ({name}) without purge, got {bodies}')
    # The context does not exist, so the cloud-side delete fails.
    expect(dialog.get_by_text('Deletion Failed'),
           'a failed delete must be reported in the dialog').to_be_visible()
    assert seed_volumes.volume_exists(name), (
        'a failed delete must keep the volume record')
    dialog.get_by_role('button', name='Force remove').click()
    expect(dialog.get_by_role('heading', name='Force remove volume'),
           'Force remove must explain what it does').to_be_visible()
    confirm = dialog.get_by_role('button', name='Force Remove', exact=True)
    expect(confirm,
           'Force Remove must wait for the acknowledgement').to_be_disabled()
    dialog.get_by_role('checkbox').check()

    since = page.recorder.mark()
    confirm.click()
    bodies = [
        json.loads(r.post_data or '{}') for r in _wait_requests(
            page,
            'POST',
            DELETE,
            since,
            what='Force Remove must send POST /volumes/delete')
    ]
    assert bodies[-1].get('names') == [name] and bodies[-1].get('purge'), (
        f'Delete must name the volume ({name}) with purge, got {bodies}')
    expect(dialog, 'Force remove must remove the volume and close the dialog'
          ).to_be_hidden()
    assert _wait_volume_gone(name), (
        'Force remove must remove the volume record from the server')
    expect(page.get_by_role('link', name=name, exact=True),
           'Force remove must remove the volume from the list').to_have_count(0)


@with_twists(
    Twist('res-volume-detail-missing',
          'res',
          expect=r'the detail page must find the volume',
          apply=_rewrite_volume_list(_drop_test_volumes)),
    Twist('res-volume-detail-config-dropped',
          'res',
          expect=r'Storage Class must show',
          apply=_rewrite_volume_list(_drop_config)),
)
def test_volume_detail_page(make_user, seed_volume, open_as, uid, twist):
    owner = make_user('owner')
    name = seed_volume(f'{uid}-data',
                       owner.user_id,
                       status='IN_USE',
                       context='e2e-ctx',
                       size='2048',
                       namespace='team-a',
                       storage_class='fast-ssd',
                       access_mode='ReadWriteMany',
                       usedby_clusters=[f'{uid}-train'])

    page = open_as(owner, f'/dashboard/volumes/{name}')
    _wait_requests(page,
                   'GET',
                   VOLUMES,
                   what='the detail page must read GET /volumes')
    expect(page.get_by_role('heading', name='Details'),
           'the detail page must find the volume').to_be_visible()
    expected = {
        'Status': 'IN_USE',
        'Name': name,
        'Infra': 'kubernetes (e2e-ctx)',
        'Type': 'k8s-pvc',
        'Size': '2Ti',
        'Name on Cloud': f'{name}-pvc',
        'Storage Class': 'fast-ssd',
        'Access Mode': 'ReadWriteMany',
        'Namespace': 'team-a',
    }
    for label, value in expected.items():
        expect(_detail(page, label),
               f'{label} must show {value}').to_have_text(value)
    # The owner is a service account, so the name carries an "SA" badge.
    expect(
        _detail(page, 'User').get_by_role('link', name=owner.name, exact=True),
        f'User must show the owner ({owner.name})').to_be_visible()
    expect(
        _detail(page, 'Used By').get_by_role('link',
                                             name=f'{uid}-train',
                                             exact=True),
        'Used By must link the cluster that mounts the volume'
    ).to_have_attribute('href', f'/dashboard/clusters/{uid}-train')

    page.get_by_role('link', name='Volumes', exact=True).last.click()
    expect(page,
           'the breadcrumb must lead back to the volume list').to_have_url(
               re.compile(r'/dashboard/volumes/?(\?.*)?$'))

    page.goto(f'/dashboard/volumes/{uid}-missing')
    expect(page.get_by_text('Volume not found.'),
           'an unknown volume must say it was not found').to_be_visible()
