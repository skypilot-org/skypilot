"""Settings: the API server configuration page.

Without plugins, Settings is one page: an admin edits the server's YAML config
and applies it; everyone else is told only admins can view it (the config
holds secrets, so the server refuses non-admin reads by default and the
dashboard must not even ask).

Applying a config changes global server state, so that test is ``exclusive``
and restores the original config in a finalizer.
"""
import json
import os
import re
import time
from typing import Any, Dict, Iterator, Tuple

from framework.twists import rewrite_request
from framework.twists import rewrite_response
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import expect
from playwright.sync_api import Page
import pytest
import requests
import yaml

CONFIG = '/workspaces/config'
SETTINGS = '/dashboard/settings'
# A harmless, schema-valid section the test adds and then removes again.
ADDED = {'kubernetes': {'provision_timeout': 37}}


def _config_file() -> Dict[str, Any]:
    with open(os.environ['SKYPILOT_GLOBAL_CONFIG'], encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _api_result(session: requests.Session,
                resp: requests.Response,
                timeout_s: float = 120) -> Tuple[bool, Any]:
    """Wait for an async request; return (succeeded, value or error)."""
    assert resp.status_code == 200, (
        f'{resp.request.method} {resp.url}: {resp.status_code} {resp.text}')
    request_id = resp.headers['X-Skypilot-Request-ID']
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = session.get(
            f'{session.base_url}/api/get',  # type: ignore[attr-defined]
            params={'request_id': request_id},
            timeout=timeout_s)
        if r.status_code == 200:
            body = r.json()
            if body.get('status') == 'SUCCEEDED':
                value = body.get('return_value')
                return True, json.loads(value) if value else None
            if body.get('status') in ('FAILED', 'CANCELLED'):
                return False, body.get('error')
        elif r.status_code >= 500:
            return False, r.text[:2000]
        time.sleep(0.3)
    return False, f'request {request_id} did not finish in {timeout_s}s'


@pytest.fixture
def restore_config(admin_api: requests.Session) -> Iterator[Dict[str, Any]]:
    """The server config as the test found it; put back afterwards."""
    ok, original = _api_result(
        admin_api, admin_api.get(f'{admin_api.base_url}{CONFIG}',
                                 timeout=60))  # type: ignore[attr-defined]
    assert ok, f'reading the server config failed: {original}'
    yield original
    ok, err = _api_result(
        admin_api,
        admin_api.post(
            f'{admin_api.base_url}{CONFIG}',  # type: ignore[attr-defined]
            json={'config': original},
            timeout=60))
    assert ok, f'restoring the server config failed: {err}'
    assert _config_file() == original, (
        'restoring the server config must rewrite the config file')


def _editor(page: Page):
    return page.locator('main .cm-content')


def _set_editor(page: Page, text: str) -> None:
    _editor(page).click()
    page.keyboard.press('ControlOrMeta+a')
    page.keyboard.press('Delete')
    page.keyboard.insert_text(text)


def _config_requests(page: Page, method: str, since: int = 0):
    return [
        r for r in page.recorder.requests[since:]
        if r.method == method and r.url.split('?')[0].endswith(CONFIG)
    ]


def _wait_apply_body(page: Page,
                     since: int,
                     timeout_s: float = 15) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        posts = page.recorder.posts(CONFIG, since)
        if posts:
            return posts[-1]
        page.wait_for_timeout(200)
    raise AssertionError(f'Apply must send POST {CONFIG}')


# --- ST-02: an admin applies a config through the editor -----------------------


@with_twists(
    Twist('srv-config-apply-drops-section',
          'srv',
          expect=r'the applied config must be saved on the server'),
    Twist('dom-config-apply-does-nothing',
          'dom',
          expect=r'Apply must send POST /workspaces/config',
          apply=swallow_click('Apply')),
    Twist('req-config-apply-sends-stale-config',
          'req',
          expect=r'Apply must send the edited config',
          apply=rewrite_request(
              CONFIG, lambda b: {
                  **b, 'config': {
                      k: v
                      for k, v in (b.get('config') or {}).items()
                      if k not in ADDED
                  }
              })),
    Twist('res-config-admin-seen-as-user',
          'res',
          expect=r'Apply must send POST /workspaces/config|an admin must see '
          r'the config editor',
          apply=rewrite_response('/users/role', lambda d, _: {
              **d, 'role': 'user'
          })),
)
@pytest.mark.exclusive
def test_admin_applies_config_through_editor(make_user, open_as, restore_config,
                                             twist):
    original = restore_config
    assert not set(ADDED) & set(original), (
        f'the test adds {list(ADDED)}, which the server config already has')
    admin = make_user('admin', role='admin')
    page = open_as(admin, SETTINGS)

    expect(page, 'Settings must open the config page').to_have_url(
        re.compile(r'/dashboard/settings/config$'))
    expect(
        page.get_by_role('heading',
                         name='Edit SkyPilot API Server Configuration'),
        'an admin must see the config editor').to_be_visible()
    for key in original:
        expect(_editor(page), f'the editor must show the current config key '
               f'{key}').to_contain_text(f'{key}:')
    assert _config_requests(
        page,
        'GET'), (f'the config page must load the config with GET {CONFIG}')

    edited = {**original, **ADDED}
    _set_editor(page, yaml.safe_dump(edited, sort_keys=False))
    since = page.recorder.mark()
    page.get_by_role('button', name='Apply').click()
    body = _wait_apply_body(page, since)
    assert body.get('config') == edited, (
        f'Apply must send the edited config {edited}, got {body.get("config")}')
    expect(page.get_by_text('Configuration saved successfully!'),
           'Apply must confirm the config was saved').to_be_visible()

    deadline = time.time() + 15
    saved = _config_file()
    while saved != edited and time.time() < deadline:
        time.sleep(0.3)
        saved = _config_file()
    assert saved == edited, (
        f'the applied config must be saved on the server: expected {edited}, '
        f'the config file has {saved}')

    page.reload()
    expect(_editor(page), 'a reloaded config page must show the applied '
           'config').to_contain_text('provision_timeout: 37')


# --- non-admins cannot read the config -----------------------------------------


@with_twists(
    Twist(
        'src-recipes_settings_nav-config-open-to-users',
        'src',
        expect=r'a user must not see the Configuration link',
        patch='twist_patches/recipes_settings_nav-config-open-to-users.patch'),
    Twist('res-health-config-unrestricted',
          'res',
          expect=r'a user must not see the Configuration link',
          apply=rewrite_response(
              '/api/health', lambda d, _: {
                  **d, 'restrict_config_to_admins': False
              })),
    Twist('res-role-reported-as-admin',
          'res',
          expect=(r'must not see the Configuration link'
                  r'|must be told only admins can view the configuration'),
          apply=rewrite_response('/users/role', lambda d, _: {
              **d, 'role': 'admin'
          })),
)
def test_non_admins_are_kept_out_of_config(make_user, open_as, twist):
    user = make_user('user')
    viewer = make_user('viewer', role='viewer')
    for ident in (user, viewer):
        page = open_as(ident, '/dashboard/clusters')
        expect(page.get_by_role('link', name='Sky Clusters'),
               'the clusters page must load').to_be_visible()
        # The link renders once the role resolves; wait for the role first.
        expect(page.locator('button[title="User Profile"]'),
               'the top bar must show the signed-in user').to_be_visible()
        _settle(page)
        expect(page.locator('a[href="/dashboard/settings"]'),
               f'a {ident.role} must not see the Configuration link'
              ).to_have_count(0)

        since = page.recorder.mark()
        page.goto(SETTINGS)
        expect(page, 'Settings must open the config page').to_have_url(
            re.compile(r'/dashboard/settings/config$'))
        expect(
            page.get_by_text('You must be an admin to view the API server '
                             'configuration.'),
            f'a {ident.role} must be told only admins can view the '
            'configuration').to_be_visible()
        expect(page.get_by_role('button', name='Apply'),
               f'a {ident.role} must not be offered Apply').to_have_count(0)
        _settle(page)
        asked = _config_requests(page, 'GET', since)
        assert not asked, (
            f'the dashboard must not request the config for a {ident.role}, '
            f'got {len(asked)} GET {CONFIG}')


def _settle(page: Page) -> None:
    try:
        page.wait_for_load_state('networkidle', timeout=30_000)
    except Exception:  # pylint: disable=broad-except
        pass
