"""Users page: the Users and Service Accounts tabs, and who may change what.

The Users tab lists people (basic-auth and system users); service accounts
have their own tab. A signed-in admin gets the undeduplicated view
(``?deduplicate=false``) with a selection column, inline role editing and a
delete button per row; system users can be neither edited nor deleted, and
non-admins get none of these controls. The Clusters and Jobs columns count
each user's clusters and active managed jobs across all users.

Tests that create users through the UI remove them in a finalizer. Tests that
assert totals ("N users", "N service accounts") are ``exclusive`` and read
the expected numbers from the DB.
"""
import json
import os
import re
import time
from typing import Any, Callable, Dict, List

from framework.fixtures import PROXY_HEADERS
from framework.fixtures import wait_ready
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
import requests
import seed_users
from seed_users import make_basic_user  # pylint: disable=unused-import

USERS = '/users'
SA_TOKENS = '/users/service-account-tokens'
SA_TAB_SEARCH = 'Search by service account name, or created by'

# --- helpers -----------------------------------------------------------------


def _open_users(open_as, ident, query: str = '') -> Page:
    page = open_as(ident, f'/dashboard/users{query}')
    return page


def _row(page: Page, cell_text: str):
    """The table row with a cell reading exactly ``cell_text``."""
    return page.get_by_role('row').filter(
        has=page.get_by_role('cell', name=cell_text, exact=True))


def _wait_posts(page: Page,
                path: str,
                since: int,
                timeout_s: float = 15) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        posts = page.recorder.posts(path, since)
        if posts:
            return posts
        page.wait_for_timeout(200)
    return []


def _got(page: Page, path: str) -> bool:
    return any(r.method == 'GET' and r.url.split('?')[0].endswith(path)
               for r in page.recorder.requests)


def _role_text(role: str):
    # Roles render lowercase with a CSS capitalize.
    return re.compile(rf'^\s*{role}\s*$', re.I)


def _swallow_clicks_on(css: str):
    """dom twist: clicks on elements matching ``css`` do nothing."""
    return run_script(f"""
      document.addEventListener('click', (e) => {{
        if (e.target.closest({json.dumps(css)})) {{
          e.stopImmediatePropagation();
          e.preventDefault();
        }}
      }}, true);
    """)


def _system_users_typed_basic(data, _body):
    """res twist: the users list stops marking system users as system."""
    return [{
        **u, 'user_type': 'basic'
    } if u.get('user_type') == 'system' else u for u in data]


def _role_says_admin(data, _body):
    """res twist: /users/role answers admin whoever asks."""
    return {**data, 'role': 'admin'}


# --- Users tab ---------------------------------------------------------------


@with_twists(
    Twist('srv-users-status-ignores-all-users',
          'srv',
          expect=r"alice's Clusters count must be 2"),
    Twist('res-system-users-typed-basic',
          'res',
          expect=r'system user .* role must not be editable',
          apply=rewrite_response(USERS, _system_users_typed_basic)),
    Twist('src-users-list-includes-service-accounts',
          'src',
          expect=r'must not be listed on the Users tab',
          patch='twist_patches/users-list-includes-service-accounts.patch'),
)
def test_users_tab_lists_users_with_counts(make_user, make_basic_user,
                                           seed_cluster, seed_job, open_as, uid,
                                           twist):
    admin = make_user('admin', role='admin')
    alice = make_basic_user('alice')
    seed_cluster(f'{uid}-c1', alice, status='UP')
    seed_cluster(f'{uid}-c2', alice, status='STOPPED')
    seed_job(f'{uid}-train', alice, status='RUNNING')
    seed_job(f'{uid}-done', alice, status='SUCCEEDED')

    page = _open_users(open_as, admin)

    expect(
        page, 'a signed-in user must get the undeduplicated view '
        '(?deduplicate=false)').to_have_url(
            re.compile(r'[?&]deduplicate=false'))
    for header in ('Name', 'User ID', 'Role', 'Type', 'Joined', 'GPUs',
                   'Clusters', 'Jobs', 'Actions'):
        expect(page.get_by_role('columnheader', name=header, exact=True),
               f'the Users table must have a {header} column').to_be_visible()

    row = _row(page, f'{uid}-alice')
    expect(row, 'basic user alice must be listed').to_have_count(1)
    expect(row.get_by_role('cell', name=alice, exact=True),
           "alice's User ID must be shown").to_be_visible()
    expect(
        row.locator('span.capitalize').first,
        'alice must show the User role').to_have_text(_role_text('user'))
    expect(row.get_by_role('cell', name=re.compile(r'^basic$', re.I)),
           'alice must be typed Basic').to_have_count(1)
    # Every cluster counts, stopped ones too; only active jobs count.
    expect(row.locator('a[href*="/clusters?"]'),
           "alice's Clusters count must be 2").to_have_text('2')
    expect(row.locator('a[href*="/jobs?"]'),
           "alice's Jobs count must be 1 (active jobs only)").to_have_text('1')

    assert _got(page, USERS), 'the Users tab must load GET /users'
    status = [b for b in page.recorder.posts('/status') if b.get('all_users')]
    assert status, ('the Clusters column must ask for every user\'s clusters '
                    f'(POST /status all_users), got '
                    f'{page.recorder.posts("/status")}')
    jobs = [
        b for b in page.recorder.posts('/jobs/queue/v2') if b.get('all_users')
    ]
    assert jobs, 'the Jobs column must ask for every user\'s managed jobs'

    expect(_row(page, admin.name),
           f'service account {admin.name} must not be listed on the Users tab'
          ).to_have_count(0)

    for sys_id in seed_users.system_user_ids():
        sys_row = _row(page, sys_id)
        expect(sys_row, f'system user {sys_id} must be listed').to_have_count(1)
        expect(
            sys_row.get_by_role('button',
                                name='Cannot edit role for system users'),
            f'system user {sys_id} role must not be editable').to_be_disabled()
        expect(sys_row.get_by_role('button', name='Cannot delete system users'),
               f'system user {sys_id} must not be deletable').to_be_disabled()
        expect(sys_row.get_by_role('checkbox'),
               f'system user {sys_id} must not be selectable').to_have_count(0)


def _drop_last_user(data, _body):
    return data[:-1] if isinstance(data, list) else data


def _drop_last_token(data, _body):
    return data[:-1] if isinstance(data, list) else data


@with_twists(
    Twist('res-users-list-drops-one',
          'res',
          expect=r'the Users tab must count \d+ users',
          apply=rewrite_response(USERS, _drop_last_user)),
    Twist('res-tokens-list-drops-one',
          'res',
          expect=r'the Service Accounts tab must count \d+ service account',
          apply=rewrite_response(SA_TOKENS, _drop_last_token)),
    Twist('src-users-list-includes-service-accounts',
          'src',
          expect=r'the Users tab must count \d+ users',
          patch='twist_patches/users-list-includes-service-accounts.patch'),
)
@pytest.mark.exclusive
def test_users_and_service_account_totals(make_user, open_as, twist):
    admin = make_user('admin', role='admin')
    page = _open_users(open_as, admin)

    listed = seed_users.listed_users()
    n = len(listed)
    expect(page.get_by_text(re.compile(rf'^{n} users?$')),
           f'the Users tab must count {n} users').to_be_visible()
    for user in listed:
        expect(
            _row(page, user.id).first,
            f'user {user.name} ({user.id}) must be listed').to_be_visible()

    page.get_by_role('button', name='Service Accounts', exact=True).click()
    tokens = seed_users.service_account_tokens()
    noun = 'service account' if len(tokens) == 1 else 'service accounts'
    expect(
        page.get_by_text(re.compile(rf'^{len(tokens)} {noun}$')),
        f'the Service Accounts tab must count {len(tokens)} {noun}',
    ).to_be_visible()


@with_twists(
    Twist('dom-no-filter-box',
          'dom',
          expect=r'"Filter users" box must be present',
          apply=remove_element('input[placeholder="Filter users"]')),
    Twist('res-roles-all-user',
          'res',
          expect=r'the Role filter must keep the viewer',
          apply=rewrite_response(
              USERS, lambda d, _b: [{
                  **u, 'role': 'user'
              } for u in d] if isinstance(d, list) else d)),
    Twist('res-user-ids-garbled',
          'res',
          expect=r'the User ID filter must keep bob',
          apply=rewrite_response(
              USERS, lambda d, _b: [{
                  **u, 'id': u['id'][::-1]
              } for u in d] if isinstance(d, list) else d)),
    Twist('dom-shared-filter-link-ignored',
          'dom',
          expect=r'a shared link with name=.* must open filtered',
          apply=run_script("""
            (() => {
              const u = new URL(window.location.href);
              if (u.searchParams.has('name')) {
                u.searchParams.delete('name');
                history.replaceState(history.state, '', u.toString());
              }
            })();
          """)),
)
def test_filter_users_by_name_and_role(make_user, make_basic_user, open_as, uid,
                                       twist):
    admin = make_user('admin', role='admin')
    make_basic_user('alice', role='viewer')
    bob_id = make_basic_user('bob', role='user')
    alice, bob = f'{uid}-alice', f'{uid}-bob'

    page = _open_users(open_as, admin)
    box = page.get_by_placeholder('Filter users')
    expect(box, '"Filter users" box must be present').to_be_visible()
    expect(_row(page, bob),
           'bob must be listed before filtering').to_have_count(1)

    # Name is the default property.
    box.fill(alice)
    box.press('Enter')
    expect(page.get_by_text(re.compile(rf'Name\s*:\s*{re.escape(alice)}')),
           'the Name filter must show as a chip').to_be_visible()
    expect(page, 'the Name filter must be in the URL').to_have_url(
        re.compile(rf'[?&]name={re.escape(alice)}'))
    expect(_row(page, alice),
           'the Name filter must keep alice').to_have_count(1)
    expect(_row(page, bob), 'the Name filter must drop bob').to_have_count(0)

    page.get_by_role('button', name='Clear filters').click()
    expect(_row(page, bob),
           'clearing filters must bring bob back').to_have_count(1)

    page.get_by_role('combobox', name='Filter Property').click()
    expect(page.get_by_role('option'),
           'the filter must offer Name, GPU, Infra, User ID and Role'
          ).to_have_text(['Name', 'GPU', 'Infra', 'User ID', 'Role'])
    page.get_by_role('option', name='Role', exact=True).click()
    box.fill('viewer')
    box.press('Enter')
    expect(page, 'the Role filter must be in the URL').to_have_url(
        re.compile(r'[?&]role=viewer'))
    expect(_row(page, alice),
           'the Role filter must keep the viewer alice').to_have_count(1)
    expect(_row(page, bob),
           'the Role filter must drop the user bob').to_have_count(0)

    page.get_by_role('button', name='Clear filters').click()
    page.get_by_role('combobox', name='Filter Property').click()
    page.get_by_role('option', name='User ID', exact=True).click()
    box.fill(bob_id)
    box.press('Enter')
    expect(page, 'the User ID filter must be in the URL').to_have_url(
        re.compile(rf'[?&]userId={bob_id}'))
    expect(_row(page, bob), 'the User ID filter must keep bob').to_have_count(1)
    expect(_row(page, alice),
           'the User ID filter must drop alice').to_have_count(0)

    # A shared link opens filtered.
    page.goto(f'/dashboard/users?deduplicate=false&name={bob}')
    wait_ready(page)
    expect(_row(page, bob), 'a shared link must list bob').to_have_count(1)
    expect(_row(page, alice),
           f'a shared link with name={bob} must open filtered to bob'
          ).to_have_count(0)


@with_twists(
    Twist('req-batch-drops-a-user',
          'req',
          expect=r'the batch must name both selected users',
          apply=rewrite_request(
              '/users/batch_update', lambda b: {
                  **b, 'user_ids': b.get('user_ids', [])[:1]
              })),
    Twist('srv-users-batch-update-wrong-role',
          'srv',
          expect=r'must show the Viewer role after the batch change'),
    Twist('dom-no-batch-bar',
          'dom',
          expect=r'selecting a user must open the batch actions bar',
          apply=remove_element('[aria-label="Batch user actions"]')),
)
def test_batch_select_and_change_role(make_user, make_basic_user, open_as,
                                      admin_api, uid, twist):
    admin = make_user('admin', role='admin')
    ids = {
        'alice': make_basic_user('alice', role='user'),
        'bob': make_basic_user('bob', role='user'),
    }
    page = _open_users(open_as, admin)
    bar = page.get_by_role('region', name='Batch user actions')
    select_all = page.get_by_role('checkbox',
                                  name='Select all users on this page')
    expect(select_all,
           'an admin must get the select-all checkbox').to_be_visible()
    expect(
        bar,
        'the batch bar must stay hidden with nothing selected').to_have_count(0)

    page.get_by_role('checkbox', name=f'Select user {uid}-alice').check()
    expect(bar,
           'selecting a user must open the batch actions bar').to_be_visible()
    expect(bar, 'the batch bar must say "1 selected"').to_contain_text(
        re.compile(r'1\s+selected'))
    assert select_all.evaluate('e => e.indeterminate'), (
        'select-all must show a mixed state with some users selected')

    page.get_by_role('checkbox', name=f'Select user {uid}-bob').check()
    expect(bar, 'the batch bar must say "2 selected"').to_contain_text(
        re.compile(r'2\s+selected'))
    bar.get_by_role('button', name='Clear').click()
    expect(bar, 'Clear must close the batch bar').to_have_count(0)
    expect(page.get_by_role('checkbox', name=f'Select user {uid}-alice'),
           'Clear must uncheck the users').not_to_be_checked()

    for name in ('alice', 'bob'):
        page.get_by_role('checkbox', name=f'Select user {uid}-{name}').check()
    bar.get_by_role('button', name='Role', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog, 'Role must open the batch role dialog').to_contain_text(
        'Change role for 2 user(s)')
    dialog.get_by_role('combobox').click()
    page.get_by_role('option', name='Viewer', exact=True).click()
    since = page.recorder.mark()
    dialog.get_by_role('button', name='Apply').click()

    posts = _wait_posts(page, '/users/batch_update', since)
    assert posts, 'Apply must send POST /users/batch_update'
    assert posts[-1].get('role') == 'viewer', (
        f'the batch must ask for role=viewer, got {posts[-1]}')
    assert sorted(posts[-1].get('user_ids', [])) == sorted(ids.values()), (
        f'the batch must name both selected users, got {posts[-1]}')
    expect(dialog, 'the batch result must count successes').to_contain_text(
        re.compile(r'\d+ succeeded'))
    dialog.get_by_role('button', name='Close', exact=True).first.click()

    for name, user_id in ids.items():
        expect(
            _row(page, f'{uid}-{name}').locator('span.capitalize').first,
            f'{name} must show the Viewer role after the batch change',
        ).to_have_text(_role_text('viewer'))
        role = seed_users.user_role(admin_api, user_id)
        assert role == 'viewer', (
            f'the batch must change both selected users: {name} is {role!r}')


# --- per-row actions ---------------------------------------------------------


@with_twists(
    Twist('srv-users-update-drops-role',
          'srv',
          expect=r'alice must show the Viewer role after saving'),
    Twist('req-update-sends-admin',
          'req',
          expect=r'alice must show the Viewer role after saving',
          apply=rewrite_request('/users/update', lambda b: {
              **b, 'role': 'admin'
          })),
    Twist('dom-edit-role-does-nothing',
          'dom',
          expect=r'Edit role must open the role picker',
          apply=_swallow_clicks_on('button[title="Edit role"]')),
)
def test_edit_role_inline(make_user, make_basic_user, open_as, admin_api, uid,
                          twist):
    admin = make_user('admin', role='admin')
    alice = make_basic_user('alice', role='user')
    page = _open_users(open_as, admin)
    row = _row(page, f'{uid}-alice')
    expect(row, 'alice must be listed').to_have_count(1)

    row.get_by_role('button', name='Edit role').click()
    picker = row.get_by_role('combobox', name='Select user role')
    expect(picker, 'Edit role must open the role picker').to_be_visible()
    options = [o.strip() for o in picker.locator('option').all_inner_texts()]
    assert options == [
        'Admin', 'User', 'Viewer'
    ], (f'the role picker must offer Admin, User and Viewer, got {options}')
    picker.select_option('viewer')
    since = page.recorder.mark()
    row.get_by_role('button', name='Save').click()

    posts = _wait_posts(page, '/users/update', since)
    assert posts, 'Save must send POST /users/update'
    assert posts[-1].get('user_id') == alice, (
        f'Save must name alice ({alice}), got {posts[-1]}')
    expect(
        row.locator('span.capitalize').first,
        'alice must show the Viewer role after saving').to_have_text(
            _role_text('viewer'))
    role = seed_users.user_role(admin_api, alice)
    assert role == 'viewer', (
        f'alice must show the Viewer role after saving: server has {role!r}')


@with_twists(
    Twist('srv-users-delete-wrong-id',
          'srv',
          expect=r'deleting .* must succeed and be confirmed'),
    Twist('dom-delete-confirm-does-nothing',
          'dom',
          expect=r'confirming must send POST /users/delete',
          apply=swallow_click('Delete')),
)
def test_delete_user_asks_then_deletes(make_user, make_basic_user, open_as, uid,
                                       twist):
    admin = make_user('admin', role='admin')
    alice_id = make_basic_user('alice')
    alice = f'{uid}-alice'
    page = _open_users(open_as, admin)
    row = _row(page, alice)
    expect(row, 'alice must be listed').to_have_count(1)

    since = page.recorder.mark()
    row.get_by_role('button', name='Delete User').click()
    dialog = page.get_by_role('dialog')
    expect(dialog,
           'Delete User must ask for confirmation by name').to_contain_text(
               f'Are you sure you want to delete user "{alice}"?')
    dialog.get_by_role('button', name='Cancel').click()
    expect(dialog, 'Cancel must close the confirmation').to_have_count(0)
    assert not page.recorder.posts('/users/delete',
                                   since), ('Cancel must not delete the user')
    expect(row, 'Cancel must keep alice listed').to_have_count(1)

    row.get_by_role('button', name='Delete User').click()
    page.get_by_role('dialog').get_by_role('button', name='Delete',
                                           exact=True).click()
    posts = _wait_posts(page, '/users/delete', since, timeout_s=5)
    assert posts, 'confirming must send POST /users/delete'
    assert posts[-1] == {
        'user_id': alice_id
    }, (f'confirming must delete alice ({alice_id}), got {posts[-1]}')
    # The table re-renders while it refreshes, so a vanished row alone does
    # not prove the deletion: wait for the confirmation first.
    expect(page.get_by_text(f'User "{alice}" deleted successfully!'),
           f'deleting {alice} must succeed and be confirmed').to_be_visible()
    expect(
        row,
        f'{alice} must disappear from the table after Delete').to_have_count(0)
    assert not seed_users.user_exists(alice_id), (
        f'{alice} must be deleted from the DB')


# --- Service Accounts tab ----------------------------------------------------


@with_twists(
    Twist('dom-sa-tab-does-nothing',
          'dom',
          expect=r'Service Accounts tab must switch the URL to '
          r'tab=service-accounts',
          apply=swallow_click('Service Accounts')),
    Twist('res-tokens-drop-ci',
          'res',
          expect=r'service account .*-ci must be listed',
          apply=rewrite_response(
              SA_TOKENS, lambda d, _b:
              [t for t in d if not t.get('token_name', '').endswith('-ci')]
              if isinstance(d, list) else d)),
    Twist('src-users-admin-sees-only-own-tokens',
          'src',
          expect=r'service account .*-ci must be listed',
          patch='twist_patches/users-admin-sees-only-own-tokens.patch'),
)
def test_service_accounts_tab(make_user, open_as, uid, twist):
    admin = make_user('admin', role='admin')
    ci = make_user('ci', role='viewer')
    page = _open_users(open_as, admin)

    page.get_by_role('button', name='Service Accounts', exact=True).click()
    expect(
        page, 'the Service Accounts tab must switch the URL to '
        'tab=service-accounts').to_have_url(
            re.compile(r'[?&]tab=service-accounts'))
    search = page.get_by_placeholder(SA_TAB_SEARCH)
    expect(
        search,
        'the Service Accounts tab must offer its search box').to_be_visible()
    search.fill(uid)
    row = _row(page, ci.name)
    expect(row, f'service account {ci.name} must be listed').to_have_count(1)
    expect(page.get_by_text(re.compile(r'^2 service accounts$')),
           'searching the test prefix must find its 2 service accounts'
          ).to_be_visible()
    expect(
        row.locator('span.capitalize').first,
        f'{ci.name} must show the Viewer role').to_have_text(
            _role_text('viewer'))
    # Both were created by the basic-auth admin, not by the account viewing.
    expect(row, f'{ci.name} must name its creator').to_contain_text('e2eadmin')
    expect(
        row,
        f'{ci.name} must be marked as created by someone else').to_contain_text(
            'Other')
    assert _got(page, SA_TOKENS), (
        'the Service Accounts tab must load GET /users/service-account-tokens')

    page.goto('/dashboard/users?tab=service-accounts')
    wait_ready(page)
    expect(
        page.get_by_placeholder(SA_TAB_SEARCH),
        'a link with tab=service-accounts must open that tab').to_be_visible()


@with_twists(
    Twist('srv-users-sa-create-ignores-role',
          'srv',
          expect=r'the new service account must have the Viewer role'),
    Twist('req-sa-create-drops-expiry',
          'req',
          expect=r'Create Token must send name, expiry and role',
          apply=rewrite_request(SA_TOKENS, lambda b: {
              **b, 'expires_in_days': 0
          })),
    Twist('dom-token-not-shown',
          'dom',
          expect=r'the new token must be shown once',
          apply=remove_element('[role="dialog"] code')),
)
def test_create_service_account_shows_token_once(make_user, open_as, admin_api,
                                                 e2e_base_url, uid, twist,
                                                 request):
    admin = make_user('admin', role='admin')
    name = f'{uid}-ci'
    request.addfinalizer(
        lambda: seed_users.delete_token_by_name(admin_api, name))
    page = _open_users(open_as, admin, '?tab=service-accounts')

    page.get_by_role('button', name='Create Service Account').click()
    dialog = page.get_by_role('dialog')
    create = dialog.get_by_role('button', name='Create Token')
    expect(create, 'Create Token must stay disabled until a name is entered'
          ).to_be_disabled()
    dialog.get_by_placeholder('e.g., ci-pipeline, monitoring-system').fill(name)
    dialog.get_by_role('combobox').click()
    options = page.get_by_role('option')
    expect(options,
           'the role picker must offer Admin, User and Viewer').to_have_text(
               ['Admin', 'User', 'Viewer'])
    page.get_by_role('option', name='Viewer', exact=True).click()
    dialog.get_by_placeholder('e.g., 30').fill('7')
    expect(create,
           'Create Token must enable once a name is entered').to_be_enabled()
    since = page.recorder.mark()
    # Wait for the server to answer: a test that fails on the request body
    # must not run its cleanup while the token is still being created.
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.
                              split('?')[0].endswith(SA_TOKENS)):
        create.click()

    posts = _wait_posts(page, SA_TOKENS, since)
    assert posts, 'Create Token must send POST /users/service-account-tokens'
    assert posts[-1] == {
        'token_name': name,
        'expires_in_days': 7,
        'role': 'viewer'
    }, f'Create Token must send name, expiry and role, got {posts[-1]}'
    expect(dialog.get_by_text(re.compile(r'will not be shown again')),
           'the new token must be shown once, with a warning').to_be_visible()
    code = dialog.locator('code')
    expect(code, 'the new token must be shown once').to_have_text(
        re.compile(r'^sky_\S+$'))
    token = code.inner_text().strip()
    dialog.get_by_role('button', name='Close', exact=True).first.click()

    me = requests.get(f'{e2e_base_url}/users/role',
                      headers={
                          'Authorization': f'Bearer {token}',
                          **PROXY_HEADERS
                      },
                      timeout=60)
    assert me.status_code == 200 and me.json().get('name') == name, (
        f'the token shown must sign in as {name}: {me.status_code} {me.text}')
    page.get_by_placeholder(SA_TAB_SEARCH).fill(name)
    expect(
        _row(page, name).locator('span.capitalize').first,
        'the new service account must have the Viewer role').to_have_text(
            _role_text('viewer'))
    created = [
        t for t in seed_users.service_account_tokens()
        if t['token_name'] == name
    ]
    assert created and created[0]['expires_at'], (
        f'the new token must expire in 7 days, got {created}')
    days = (created[0]['expires_at'] - time.time()) / 86400
    assert 6 < days <= 7.01, f'the new token must expire in 7 days, got {days}'


def _role_says_viewer(data, _body):
    """res twist: /users/role answers viewer whoever asks."""
    return {**data, 'role': 'viewer'}


def _expires_in_days(name: str) -> float:
    tokens = [
        t for t in seed_users.service_account_tokens()
        if t['token_name'] == name
    ]
    assert tokens and tokens[0]['expires_at'], (
        f'service account {name} must have an expiry, got {tokens}')
    return (tokens[0]['expires_at'] - time.time()) / 86400


def _signs_in(e2e_base_url: str, token: str) -> int:
    return requests.get(f'{e2e_base_url}/users/role',
                        headers={
                            'Authorization': f'Bearer {token}',
                            **PROXY_HEADERS
                        },
                        timeout=60).status_code


@with_twists(
    Twist('res-user-reported-viewer',
          'res',
          expect=r'a user must get the create dialog',
          apply=rewrite_response('/users/role', _role_says_viewer)),
    Twist('res-user-reported-admin',
          'res',
          expect=r'a user must (start on the User role|not be offered the '
          r'Admin role)',
          apply=rewrite_response('/users/role', _role_says_admin)),
    Twist('dom-no-rotate-button',
          'dom',
          expect=r'a user must get Rotate on their own service account',
          apply=remove_element('button:has(svg.lucide-rotate-cw)')),
    Twist('srv-users-sa-rotate-ignores-expiry',
          'srv',
          expect=r'the rotated token must expire in 30 days'),
)
def test_user_manages_own_service_account(make_user, open_as, admin_api,
                                          e2e_base_url, uid, twist, request):
    """A non-admin creates, rotates and deletes a service account of their
    own, as the server allows; only admins may grant the admin role."""
    user = make_user('user', role='user')
    name = f'{uid}-own'
    request.addfinalizer(
        lambda: seed_users.delete_token_by_name(admin_api, name))
    page = _open_users(open_as, user, '?tab=service-accounts')

    page.get_by_role('button', name='Create Service Account').click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_role('button', name='Create Token'),
           'a user must get the create dialog').to_be_visible()
    # The server's default role is admin here, which a user cannot grant.
    role_picker = dialog.get_by_role('combobox')
    expect(role_picker, 'a user must start on the User role').to_have_text(
        _role_text('user'))
    role_picker.click()
    expect(page.get_by_role('option'),
           'a user must not be offered the Admin role').to_have_text(
               ['User', 'Viewer'])
    page.get_by_role('option', name='User', exact=True).click()
    dialog.get_by_placeholder('e.g., ci-pipeline, monitoring-system').fill(name)
    dialog.get_by_placeholder('e.g., 30').fill('7')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.
                              split('?')[0].endswith(SA_TOKENS)) as created:
        dialog.get_by_role('button', name='Create Token').click()
    assert created.value.status == 200, (
        f'the server must let a user create a service account: '
        f'{created.value.status} {created.value.text()}')
    code = dialog.locator('code')
    expect(code, 'the new token must be shown once').to_have_text(
        re.compile(r'^sky_\S+$'))
    old_token = code.inner_text().strip()
    dialog.get_by_role('button', name='Close', exact=True).first.click()

    page.get_by_placeholder(SA_TAB_SEARCH).fill(name)
    row = _row(page, name)
    expect(row, f'service account {name} must be listed').to_have_count(1)
    expect(
        row.locator('span.capitalize').first,
        f'{name} must have the User role').to_have_text(_role_text('user'))

    # Rotate with a new expiry.
    rotate = row.locator('button:has(svg.lucide-rotate-cw)')
    expect(
        rotate,
        'a user must get Rotate on their own service account').to_be_visible()
    rotate.click()
    expect(
        page.get_by_text('is logged in as non-admin'),
        'a user must not be denied rotating their own token').to_have_count(0)
    dialog = page.get_by_role('dialog')
    dialog.get_by_placeholder(
        'Leave empty to preserve current expiration').fill('30')
    with page.expect_response(
            lambda r: r.request.method == 'POST' and r.url.split('?')[
                0].endswith(f'{SA_TOKENS}/rotate')) as rotated:
        dialog.get_by_role('button', name='Rotate Token').click()
    assert rotated.value.status == 200, (
        f'the server must let a user rotate their own token: '
        f'{rotated.value.status} {rotated.value.text()}')
    code = dialog.locator('code')
    expect(code, 'the rotated token must be shown once').to_have_text(
        re.compile(r'^sky_\S+$'))
    new_token = code.inner_text().strip()
    dialog.get_by_role('button', name='Close', exact=True).first.click()
    assert new_token != old_token, 'rotating must issue a new token'
    assert _signs_in(
        e2e_base_url,
        old_token) == 401, ('the old token must stop working after a rotate')
    assert _signs_in(e2e_base_url,
                     new_token) == 200, ('the rotated token must sign in')
    days = _expires_in_days(name)
    assert 29 < days <= 30.01, (
        f'the rotated token must expire in 30 days, got {days}')

    # Delete.
    row.locator('button:has(svg.lucide-trash2, svg.lucide-trash-2)').click()
    expect(
        page.get_by_text('is logged in as non-admin'),
        'a user must not be denied deleting their own token').to_have_count(0)
    dialog = page.get_by_role('dialog')
    # The open dialog hides the table from role queries, so wait for the
    # server to answer and the dialog to close before looking at the row.
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.
                              split('?')[0].endswith(f'{SA_TOKENS}/delete'),
                              timeout=60_000) as deleted:
        dialog.get_by_role('button', name='Delete Token').click()
    assert deleted.value.status == 200, (
        f'the server must let a user delete their own token: '
        f'{deleted.value.status} {deleted.value.text()}')
    expect(dialog, 'the delete dialog must close').to_have_count(0)
    expect(
        row,
        f'the deleted service account {name} must disappear').to_have_count(0)
    assert not [
        t for t in seed_users.service_account_tokens()
        if t['token_name'] == name
    ], f'service account {name} must be deleted on the server'


# --- roles -------------------------------------------------------------------


@with_twists(
    Twist('res-role-says-admin',
          'res',
          expect=r'a non-admin .* must not get',
          apply=rewrite_response('/users/role', _role_says_admin)),
    Twist('src-users-role-endpoint-says-admin',
          'src',
          expect=r'a non-admin .* must not get',
          patch='twist_patches/users-role-endpoint-says-admin.patch'),
    Twist('dom-no-select-all',
          'dom',
          expect=r'an admin must get batch selection',
          apply=remove_element(
              'input[aria-label="Select all users on this page"]')),
)
def test_users_page_controls_by_role(make_user, make_basic_user, open_as, uid,
                                     twist):
    alice = f'{uid}-alice'
    make_basic_user('alice')
    admin = make_user('admin', role='admin')
    user = make_user('user', role='user')
    viewer = make_user('viewer', role='viewer')

    page = _open_users(open_as, admin)
    row = _row(page, alice)
    expect(row, 'alice must be listed').to_have_count(1)
    expect(page.get_by_role('checkbox', name='Select all users on this page'),
           'an admin must get batch selection').to_be_visible()
    expect(row.get_by_role('button', name='Edit role'),
           'an admin must get Edit role').to_be_enabled()
    expect(row.get_by_role('button', name='Delete User'),
           'an admin must get Delete User').to_be_enabled()
    expect(page.get_by_role('button', name='+ New User'),
           'an admin must get + New User').to_be_visible()

    for ident in (user, viewer):
        page = _open_users(open_as, ident)
        row = _row(page, alice)
        who = f'a non-admin ({ident.role})'
        expect(row, f'{who} must still see alice').to_have_count(1)
        expect(page.get_by_role('checkbox'),
               f'{who} must not get batch selection').to_have_count(0)
        expect(row.get_by_role('button', name='Edit role'),
               f'{who} must not get Edit role').to_have_count(0)
        expect(row.get_by_role('button', name='Delete User'),
               f'{who} must not get Delete User').to_have_count(0)
        expect(page.get_by_role('button', name='+ New User'),
               f'{who} must not get + New User').to_have_count(0)
        expect(
            row.get_by_role('button',
                            name='You can only reset your own password'),
            f'{who} must not reset another user\'s password').to_be_disabled()

    # A viewer asking to create a service account is told why not.
    page.get_by_role('button', name='Service Accounts', exact=True).click()
    page.get_by_role('button', name='Create Service Account').click()
    denied = page.get_by_role('dialog')
    expect(denied, f'a non-admin ({viewer.role}) must not get the create '
           'dialog').to_contain_text('Permission Denied')
    expect(denied, 'the denial must say why').to_contain_text(
        'cannot create service account tokens')


@with_twists(
    Twist('res-viewer-reported-admin',
          'res',
          expect=r'the viewer must not be badged Admin',
          apply=rewrite_response('/users/role', _role_says_admin)),
    Twist('dom-no-user-menu',
          'dom',
          expect=r'the user menu must be offered',
          apply=remove_element('button[title="User Profile"]')),
)
def test_user_menu_shows_name_and_role(make_user, open_as, twist):
    admin = make_user('admin', role='admin')
    viewer = make_user('viewer', role='viewer')

    for ident, badge in ((admin, 'Admin'), (viewer, 'User')):
        page = open_as(ident, '/dashboard/clusters')
        menu_button = page.locator('button[title="User Profile"]')
        expect(menu_button, 'the user menu must be offered').to_be_visible()
        menu_button.click()
        menu = page.locator('div.absolute.right-0.mt-2')
        expect(menu, f'the user menu must show {ident.name}').to_contain_text(
            ident.name)
        if ident.role == 'admin':
            expect(menu.get_by_text('Admin', exact=True),
                   'an admin must be badged Admin').to_be_visible()
        else:
            # Non-admin roles share one "User" badge.
            expect(menu.get_by_text('Admin', exact=True),
                   'the viewer must not be badged Admin').to_have_count(0)
            expect(menu.get_by_text(badge, exact=True),
                   'the viewer must be badged User').to_be_visible()


# --- authentication ----------------------------------------------------------


def _serve_dashboard_without_auth(context: BrowserContext) -> None:
    """res twist: a cache in front of the server hands the dashboard page to
    requests that carry no credentials."""

    def handler(route: Route) -> None:
        if 'authorization' in route.request.headers:
            route.continue_()
            return
        route.fulfill(status=200,
                      content_type='text/html',
                      body='<html><body>cached dashboard</body></html>')

    context.route('**/dashboard/clusters', handler)


@with_twists(
    Twist('res-dashboard-served-without-auth',
          'res',
          expect=r'an unauthenticated request for the dashboard must be '
          r'refused with 401',
          apply=_serve_dashboard_without_auth),
    Twist('src-users-dashboard-skips-basic-auth',
          'src',
          expect=r'an unauthenticated request for the dashboard must be '
          r'refused with 401',
          patch='twist_patches/users-dashboard-skips-basic-auth.patch'),
    Twist('dom-signed-in-no-user-menu',
          'dom',
          expect=r'signing in with basic auth must show the admin\'s name',
          apply=remove_element('button[title="User Profile"]')),
)
def test_dashboard_requires_basic_auth(browser, e2e_base_url, twist):
    user, password = os.environ['E2E_ADMIN_USER'], os.environ['E2E_ADMIN_PW']
    contexts: List[BrowserContext] = []

    def new_context(**kwargs) -> BrowserContext:
        ctx = browser.new_context(base_url=e2e_base_url,
                                  extra_http_headers=PROXY_HEADERS,
                                  **kwargs)
        if twist is not None and twist.apply is not None:
            twist.apply(ctx)
        contexts.append(ctx)
        return ctx

    try:
        page = new_context().new_page()
        resp = page.goto('/dashboard/clusters')
        assert resp is not None and resp.status == 401, (
            'an unauthenticated request for the dashboard must be refused '
            f'with 401, got {resp.status if resp else None}')
        assert 'basic' in (resp.headers.get('www-authenticate') or
                           '').lower(), (
                               'the refusal must ask for basic auth, got '
                               f'{resp.headers}')

        page = new_context(http_credentials={
            'username': user,
            'password': 'wrong-password'
        }).new_page()
        resp = page.goto('/dashboard/clusters')
        assert resp is not None and resp.status == 401, (
            'a wrong password must be refused with 401, got '
            f'{resp.status if resp else None}')

        page = new_context(http_credentials={
            'username': user,
            'password': password
        }).new_page()
        resp = page.goto('/dashboard/clusters')
        assert resp is not None and resp.status == 200, (
            f'the right password must open the dashboard, got '
            f'{resp.status if resp else None}')
        wait_ready(page)
        menu_button = page.locator('button[title="User Profile"]')
        expect(menu_button, 'signing in with basic auth must show the '
               "admin's name").to_be_visible()
        menu_button.click()
        expect(page.get_by_text(user, exact=True),
               "signing in with basic auth must show the admin's name"
              ).to_be_visible()
    finally:
        for ctx in contexts:
            ctx.close()
