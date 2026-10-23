"""Recipes hub and recipe detail.

The hub lists every recipe in three places: "Pinned Recipes" (cards), "My
Recipes" (the viewer's unpinned recipes, cards) and "All Recipes" (a table).
The server ships four default recipes that are pinned and cannot be edited or
deleted. Regressions here hide recipes, let a click do nothing, or create and
delete the wrong thing.

Recipes are global: every user sees every recipe. Tests that assert on the
hub's totals are ``exclusive``; the rest assert only on recipes they created
and run in parallel.
"""
import json
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

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
import seed_recipes

RECIPES_LIST = '/recipes/list'
RECIPES_GET = '/recipes/get'
RECIPES_CREATE = '/recipes/create'
RECIPES_DELETE = '/recipes/delete'
RECIPES_PIN = '/recipes/pin'
DEFAULT_RECIPES = [
    'basic-cluster', 'basic-managed-job', 'basic-pool', 'basic-volume'
]
# What the viewer should read when the server refuses to create a recipe.
READABLE_PERMISSION = re.compile(
    r'permission|not allowed|not authori[sz]ed|forbidden|viewer', re.I)
RAW_CLIENT_ERROR = re.compile(r'apiClient\.fetch|Initial API request')


@pytest.fixture
def recipes() -> Iterator['_Recipes']:
    """Seed recipes, or track ones a test creates through the UI; every one
    is deleted afterwards."""
    r = _Recipes()
    yield r
    for name in r.names:
        seed_recipes.delete(name)


class _Recipes:

    def __init__(self):
        self.names: List[str] = []

    def seed(self, name: str, owner, **kwargs) -> str:
        self.names.append(name)
        return seed_recipes.recipe(name, owner.user_id, owner.name, **kwargs)

    def track(self, name: str) -> str:
        self.names.append(name)
        return name


# --- page helpers --------------------------------------------------------------


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


def _response_status(page: Page, path: str, since: int) -> Optional[int]:
    for req in page.recorder.requests[since:]:
        if req.method == 'POST' and req.url.split('?')[0].endswith(path):
            resp = req.response()
            return None if resp is None else resp.status
    return None


def _section(page: Page, title: str):
    """The card row (Pinned / My Recipes) under heading ``title``."""
    return page.locator('div.mb-6').filter(
        has=page.get_by_role('heading', name=title, exact=True))


def _card(page: Page, section: str, name: str):
    return _section(page, section).get_by_role('heading', name=name, exact=True)


def _row(page: Page, name: str):
    return page.get_by_role('row').filter(
        has=page.get_by_role('link', name=name, exact=True))


def _fill_yaml(page: Page, scope, text: str) -> None:
    editor = scope.locator('.cm-content')
    editor.click()
    page.keyboard.press('ControlOrMeta+a')
    page.keyboard.press('Delete')
    page.keyboard.insert_text(text)


def _toast(page: Page, text: str):
    return page.locator('#toast-container').get_by_text(text)


# --- twist helpers -------------------------------------------------------------


def _rewrite_result(paths: List[str], fn: Callable[[Any], Any]):
    """res twist: rewrite the async result of POSTs to each ``*path``.

    The POST returns a request id; the result comes from /api/get. Remember
    the ids, then rewrite those results."""

    def apply(context: BrowserContext) -> None:
        ids = set()

        def on_post(route: Route) -> None:
            resp = route.fetch()
            ids.add(resp.headers.get('x-skypilot-request-id'))
            route.fulfill(response=resp)

        def on_get(route: Route) -> None:
            resp = route.fetch()
            rid = route.request.url.split('request_id=')[-1].split('&')[0]
            if rid not in ids or resp.status != 200:
                route.fulfill(response=resp)
                return
            data = resp.json()
            if data.get('return_value'):
                data['return_value'] = json.dumps(
                    fn(json.loads(data['return_value'])))
            route.fulfill(response=resp, json=data)

        for path in paths:
            context.route(f'**{path}', on_post)
        context.route('**/api/get?*', on_get)

    return apply


def _each_recipe(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
    """Apply ``fn`` to a recipe result, or to every recipe of a list."""

    def rewrite(value):
        if isinstance(value, list):
            return [fn(dict(r)) for r in value]
        if isinstance(value, dict):
            return fn(dict(value))
        return value

    return rewrite


def _unpinned(r):
    r['pinned'] = False
    return r


def _defaults_editable(r):
    if r.get('name') in DEFAULT_RECIPES:
        r['is_editable'] = True
    return r


def _as_cluster(r):
    r['recipe_type'] = 'cluster'
    return r


# --- R-01: hub sections --------------------------------------------------------


@with_twists(
    Twist('srv-recipes-list-pinned-only',
          'srv',
          expect=r'My Recipes must list the owner.s own recipe'),
    Twist('req-recipes-list-volume-only',
          'req',
          expect=(r'the hub must list every recipe'
                  r'|Pinned Recipes must list default recipe basic-cluster'),
          apply=rewrite_request(RECIPES_LIST, lambda b: {
              **b, 'recipe_type': 'volume'
          })),
    Twist('res-recipes-pinned-flag-dropped',
          'res',
          expect=r'Pinned Recipes must list default recipe basic-cluster',
          apply=_rewrite_result([RECIPES_LIST], _each_recipe(_unpinned))),
)
@pytest.mark.exclusive
def test_hub_lists_pinned_mine_and_all(make_user, open_as, recipes, uid, twist):
    owner = make_user('owner')
    mine = recipes.seed(f'{uid}-mine', owner, description='e2e owned recipe')

    page = open_as(owner, '/dashboard/recipes')

    body = _wait_post(page, RECIPES_LIST, 0, 'the hub must ask for its recipes')
    assert (not body.get('pinned_only') and not body.get('my_recipes_only') and
            body.get('recipe_type') is None), (
                'the hub must list every recipe, with no pinned / mine / type '
                f'filter, got {body}')
    pinned = seed_recipes.pinned_names()
    total = seed_recipes.count_recipes()
    for name in DEFAULT_RECIPES:
        assert name in pinned, f'default recipe {name} must be pinned in the DB'
        expect(
            _card(page, 'Pinned Recipes', name),
            f'Pinned Recipes must list default recipe {name}').to_be_visible()
    expect(
        _section(page, 'Pinned Recipes').get_by_text(f'({len(pinned)})',
                                                     exact=True),
        f'Pinned Recipes must count {len(pinned)} recipes').to_be_visible()
    expect(_card(page, 'My Recipes', mine),
           "My Recipes must list the owner's own recipe").to_be_visible()
    expect(
        _section(page, 'My Recipes').get_by_text('(1)', exact=True),
        'My Recipes must count exactly the one recipe the owner has'
    ).to_be_visible()
    for name in DEFAULT_RECIPES + [mine]:
        expect(_row(page, name),
               f'All Recipes must list {name}').to_have_count(1)
    expect(page.get_by_text(f'{total} of {total}', exact=True),
           f'All Recipes must read "{total} of {total}"').to_be_visible()


# --- R-02: pin / unpin ---------------------------------------------------------


@with_twists(
    Twist('srv-recipes-pin-always-true',
          'srv',
          expect=r'unpinning must take the recipe out of Pinned Recipes'),
    Twist('dom-no-unpin-on-card',
          'dom',
          expect=r'the pinned card must offer Unpin',
          apply=remove_element('div.relative > button[title="Unpin recipe"]')),
    Twist('req-pin-sends-unpin',
          'req',
          expect=(r'Pin must send pinned=true'
                  r'|pinning must move the recipe into Pinned Recipes'),
          apply=rewrite_request(RECIPES_PIN, lambda b: {
              **b, 'pinned': False
          })),
)
def test_pin_and_unpin_own_recipe(make_user, open_as, recipes, uid, twist):
    owner = make_user('owner')
    name = recipes.seed(f'{uid}-pin', owner)

    page = open_as(owner, '/dashboard/recipes')
    expect(_card(page, 'My Recipes', name),
           'an unpinned recipe must start in My Recipes').to_be_visible()

    since = page.recorder.mark()
    _row(page, name).get_by_role('button', name='Pin recipe').click()
    body = _wait_post(page, RECIPES_PIN, since, 'Pin must send /recipes/pin')
    assert body.get('recipe_name') == name and body.get('pinned') is True, (
        f'Pin must send pinned=true for {name}, got {body}')
    expect(_card(page, 'Pinned Recipes', name),
           'pinning must move the recipe into Pinned Recipes').to_be_visible()
    expect(_card(page, 'My Recipes', name),
           'a pinned recipe must leave My Recipes').to_have_count(0)
    assert seed_recipes.get(name)['pinned'], (
        'pinning must move the recipe into Pinned Recipes in the DB')

    unpin = _section(page, 'Pinned Recipes').locator('div.relative').filter(
        has=page.get_by_role('heading', name=name, exact=True)).get_by_role(
            'button', name='Unpin recipe')
    expect(unpin, 'the pinned card must offer Unpin').to_be_visible()
    since = page.recorder.mark()
    unpin.click()
    body = _wait_post(page, RECIPES_PIN, since, 'Unpin must send /recipes/pin')
    assert body.get('recipe_name') == name and body.get('pinned') is False, (
        f'Unpin must send pinned=false for {name}, got {body}')
    expect(
        _card(page, 'Pinned Recipes', name),
        'unpinning must take the recipe out of Pinned Recipes').to_have_count(0)
    expect(_card(page, 'My Recipes', name),
           'an unpinned recipe must return to My Recipes').to_be_visible()
    assert not seed_recipes.get(name)['pinned'], (
        'unpinning must take the recipe out of Pinned Recipes in the DB')


# --- R-03: default recipes are read-only ---------------------------------------


@with_twists(
    Twist('res-defaults-marked-editable',
          'res',
          expect=r'Delete of default recipe .* must be disabled',
          apply=_rewrite_result([RECIPES_LIST, RECIPES_GET],
                                _each_recipe(_defaults_editable))),
    Twist('dom-default-delete-reason-hidden',
          'dom',
          expect=r'must say "Default recipes cannot be deleted"',
          apply=run_script("""
            new MutationObserver(() => document
              .querySelectorAll('[title="Default recipes cannot be deleted"]')
              .forEach(b => b.removeAttribute('title')))
              .observe(document, {childList: true, subtree: true});
          """)),
)
def test_default_recipes_cannot_be_deleted(make_user, open_as, twist):
    user = make_user('user')
    page = open_as(user, '/dashboard/recipes')

    for name in DEFAULT_RECIPES:
        delete = _row(page, name).locator('button').nth(1)
        expect(delete, f'Delete of default recipe {name} must be disabled'
              ).to_be_disabled()
        expect(
            delete, f'Delete of default recipe {name} must say '
            '"Default recipes cannot be deleted"').to_have_attribute(
                'title', 'Default recipes cannot be deleted')

    page.goto('/dashboard/recipes/basic-cluster')
    expect(page.get_by_role('heading', name='basic-cluster', level=1),
           'the default recipe detail page must load').to_be_visible()
    for label in ('Edit', 'Delete'):
        button = page.get_by_role('button', name=label, exact=True)
        expect(button,
               f'{label} of default recipe basic-cluster must be disabled'
              ).to_be_disabled()
    expect(
        page.get_by_role('button', name='Delete', exact=True),
        'Delete of default recipe basic-cluster must say '
        '"Default recipes cannot be deleted"').to_have_attribute(
            'title', 'Default recipes cannot be deleted')
    assert not page.recorder.posts(RECIPES_DELETE), (
        'nothing may send /recipes/delete for a default recipe')


# --- R-04: New Recipe dialog ---------------------------------------------------


@with_twists(
    Twist('dom-no-new-recipe-button',
          'dom',
          expect=r'"New Recipe" must be offered',
          apply=remove_button('New Recipe')),
    Twist('dom-type-missing-volume',
          'dom',
          expect=r'Type must offer Volume',
          apply=run_script("""
            new MutationObserver(() => document
              .querySelectorAll('[role="option"]')
              .forEach(o => { if (o.innerText.trim() === 'Volume') o.remove(); }))
              .observe(document, {childList: true, subtree: true});
          """)),
)
def test_new_recipe_dialog_fields(make_user, open_as, twist):
    user = make_user('user')
    page = open_as(user, '/dashboard/recipes')

    new = page.get_by_role('button', name='New Recipe')
    expect(new, '"New Recipe" must be offered').to_be_visible()
    new.click()
    dialog = page.get_by_role('dialog')
    expect(
        dialog.get_by_role('heading', name='Create New Recipe'),
        'New Recipe must open the "Create New Recipe" dialog').to_be_visible()
    expect(dialog.get_by_label('Name *'),
           'the dialog must ask for a Name').to_be_visible()
    expect(dialog.get_by_text('Type *'),
           'the dialog must ask for a Type').to_be_visible()
    type_select = dialog.get_by_role('combobox')
    expect(type_select, 'Type must default to Cluster').to_have_text('Cluster')
    expect(dialog.locator('.cm-content'),
           'the YAML editor must start with a cluster example').to_contain_text(
               'Hello, SkyPilot!')
    expect(dialog.get_by_text('This recipe will be visible to everyone'),
           'the dialog must warn that recipes are visible to everyone'
          ).to_be_visible()

    type_select.click()
    for label in ('Cluster', 'Managed Job', 'Job Pool', 'Volume'):
        expect(page.get_by_role('option', name=label, exact=True),
               f'Type must offer {label}').to_be_visible()
    page.get_by_role('option', name='Managed Job', exact=True).click()
    expect(type_select,
           'picking Managed Job must show it as the Type').to_have_text(
               'Managed Job')
    expect(
        dialog.locator('.cm-content'),
        'switching Type must swap in the managed-job example').to_contain_text(
            'Running managed job...')

    dialog.get_by_role('button', name='Cancel').click()
    expect(dialog, 'Cancel must close the dialog').to_be_hidden()
    assert not page.recorder.posts(RECIPES_CREATE), (
        'Cancel must not send /recipes/create')


# --- R-05: create and delete through the UI ------------------------------------


def _create_through_dialog(page: Page, name: str, description: str,
                           content: str) -> None:
    page.get_by_role('button', name='New Recipe').click()
    dialog = page.get_by_role('dialog')
    expect(
        dialog.get_by_role('heading', name='Create New Recipe'),
        'New Recipe must open the "Create New Recipe" dialog').to_be_visible()
    dialog.get_by_label('Name *').fill(name)
    dialog.get_by_label('Description').fill(description)
    dialog.get_by_role('combobox').click()
    page.get_by_role('option', name='Managed Job', exact=True).click()
    _fill_yaml(page, dialog, content)
    dialog.get_by_role('button', name='Create Recipe').click()


@with_twists(
    Twist('srv-recipes-create-as-cluster',
          'srv',
          expect=r'the new recipe must be listed as a Job'),
    Twist('srv-recipes-delete-wrong-name',
          'srv',
          expect=r'deleting must confirm with a toast'),
    Twist('dom-create-does-nothing',
          'dom',
          expect=r'Create Recipe must send /recipes/create',
          apply=swallow_click('Create Recipe')),
    Twist('dom-delete-confirm-does-nothing',
          'dom',
          expect=r'confirming must send /recipes/delete',
          apply=swallow_click('Delete')),
)
def test_create_and_delete_recipe_through_ui(make_user, open_as, recipes, uid,
                                             twist):
    author = make_user('author')
    name = recipes.track(f'{uid}-made')
    content = seed_recipes.JOB_YAML.replace('echo e2e', f'echo {uid}')

    page = open_as(author, '/dashboard/recipes')
    since = page.recorder.mark()
    _create_through_dialog(page, name, 'made by e2e', content)

    body = _wait_post(page, RECIPES_CREATE, since,
                      'Create Recipe must send /recipes/create')
    assert (body.get('name') == name and body.get('recipe_type') == 'job' and
            body.get('description') == 'made by e2e' and
            f'echo {uid}' in body.get('content', '')), (
                'Create Recipe must send the name, type, description and '
                f'YAML that were entered, got {body}')
    expect(_toast(page, 'Recipe created successfully!'),
           'creating must confirm with a toast').to_be_visible()
    expect(page.get_by_role('dialog'),
           'the dialog must close after creating').to_be_hidden()
    expect(_card(page, 'My Recipes',
                 name), "the new recipe must appear in the author's My Recipes"
          ).to_be_visible()
    row = _row(page, name)
    expect(
        row.get_by_role('cell').first,
        'the new recipe must be listed as a Job').to_have_text('Job')
    expect(
        row.get_by_role('cell').nth(3),
        'the new recipe must be owned by its author').to_have_text(author.name)
    stored = seed_recipes.get(name)
    assert stored and stored['user_id'] == author.user_id and stored[
        'recipe_type'] == 'job', (
            "the server must store the recipe as the author's Managed Job, "
            f'got {stored}')

    since = page.recorder.mark()
    row.get_by_role('button', name='Delete recipe').click()
    dialog = page.get_by_role('dialog')
    expect(
        dialog.get_by_text(f'Are you sure you want to delete "{name}"?'),
        'Delete must ask for confirmation, naming the recipe').to_be_visible()
    dialog.get_by_role('button', name='Delete').click()
    body = _wait_post(page, RECIPES_DELETE, since,
                      'confirming must send /recipes/delete')
    assert body.get('recipe_name') == name, (
        f'confirming must delete {name}, got {body}')
    expect(_toast(page, 'Recipe deleted successfully!'),
           'deleting must confirm with a toast').to_be_visible()
    expect(page.get_by_role('dialog'),
           'the confirmation must close after deleting').to_be_hidden()
    expect(_row(page, name),
           'a deleted recipe must leave the table').to_have_count(0)
    assert seed_recipes.get(name) is None, (
        'a deleted recipe must leave the DB')


# --- R-05 (viewer): a refused create must explain itself -----------------------

_VIEWER_CREATE_XFAIL = (
    'Known UX bug: when the server refuses a viewer (403), the dialog shows the '
    'raw client error "Error in apiClient.fetch for /recipes/create: Error: '
    'Initial API request /recipes/create failed with status 403" instead of '
    'a readable permission message.')


def _xfail_baseline(parametrize, reason: str):
    """Mark only the baseline run xfail(strict): the twisted runs must still
    be caught by the assertions that come before the known-bad one."""
    argname, params = parametrize.mark.args[0], parametrize.mark.args[1]
    out = []
    for p in params:
        if p.id == 'baseline':
            p = pytest.param(*p.values,
                             id=p.id,
                             marks=list(p.marks) +
                             [pytest.mark.xfail(strict=True, reason=reason)])
        out.append(p)
    return pytest.mark.parametrize(argname, out)


@_xfail_baseline(
    with_twists(
        Twist('dom-viewer-create-does-nothing',
              'dom',
              expect=r'Create Recipe must send /recipes/create',
              apply=swallow_click('Create Recipe')),
        Twist('dom-create-error-hidden',
              'dom',
              expect=r'the dialog must say why the recipe was not created',
              apply=remove_element('p.text-red-800')),
        Twist(
            'src-recipes_settings_nav-viewer-can-create-recipes',
            'src',
            expect=r'the server must refuse a viewer with 403',
            patch=
            'twist_patches/recipes_settings_nav-viewer-can-create-recipes.patch'
        ),
    ), _VIEWER_CREATE_XFAIL)
def test_viewer_create_recipe_shows_permission_error(make_user, open_as,
                                                     recipes, uid, twist):
    viewer = make_user('viewer', role='viewer')
    name = recipes.track(f'{uid}-viewer')

    page = open_as(viewer, '/dashboard/recipes')
    since = page.recorder.mark()
    _create_through_dialog(page, name, 'viewer attempt', seed_recipes.JOB_YAML)

    _wait_post(page, RECIPES_CREATE, since,
               'Create Recipe must send /recipes/create')
    status = _response_status(page, RECIPES_CREATE, since)
    assert status == 403, (
        f'the server must refuse a viewer with 403, got {status}')
    dialog = page.get_by_role('dialog')
    error = dialog.locator('p.text-red-800')
    expect(
        error,
        'the dialog must say why the recipe was not created').to_be_visible()
    expect(dialog.get_by_role('heading', name='Create New Recipe'),
           'the dialog must stay open after a refused create').to_be_visible()
    assert seed_recipes.get(name) is None, (
        'a viewer must not be able to create a recipe')
    text = error.inner_text()
    assert READABLE_PERMISSION.search(text) and not RAW_CLIENT_ERROR.search(
        text), ('a refused create must show a readable permission message, '
                f'got: {text}')


# --- R-06: detail page ---------------------------------------------------------


@with_twists(
    Twist('res-detail-recipe-type-wrong',
          'res',
          expect=r'Launch Command must be "sky jobs launch recipes:',
          apply=_rewrite_result([RECIPES_GET], _each_recipe(_as_cluster))),
    Twist('req-detail-asks-for-other-recipe',
          'req',
          expect=r'the detail page must ask for the recipe in its URL',
          apply=rewrite_request(RECIPES_GET, lambda b: {
              **b, 'recipe_name': 'basic-cluster'
          })),
    Twist('dom-no-copy-to-new',
          'dom',
          expect=r'"Copy to New" must be offered',
          apply=remove_button('Copy to New')),
)
def test_recipe_detail_shows_command_yaml_and_copy(make_user, open_as, recipes,
                                                   uid, twist):
    author = make_user('author')
    reader = make_user('reader')
    content = seed_recipes.JOB_YAML.replace('echo e2e', f'echo {uid}')
    name = recipes.seed(f'{uid}-detail',
                        author,
                        content=content,
                        description='detail page recipe')

    page = open_as(reader, f'/dashboard/recipes/{name}')

    body = _wait_post(page, RECIPES_GET, 0,
                      'the detail page must ask for its recipe')
    assert body.get('recipe_name') == name, (
        f'the detail page must ask for the recipe in its URL ({name}), '
        f'got {body}')
    expect(
        page.get_by_role('heading', name=name, level=1),
        'the detail page must be titled with the recipe name').to_be_visible()
    expect(
        page.locator('code').filter(has_text='recipes:'),
        f'Launch Command must be "sky jobs launch recipes:{name}"'
    ).to_have_text(f'sky jobs launch recipes:{name}')
    expect(page.locator('main .cm-content'),
           'the detail page must show the recipe YAML').to_contain_text(
               f'echo {uid}')
    expect(page.get_by_text('detail page recipe'),
           'the detail page must show the description').to_be_visible()
    expect(page.get_by_text(f'Authored by{author.name}'),
           'the detail page must name the author').to_be_visible()
    for label in ('Share', 'Pin'):
        expect(page.get_by_role('button', name=label, exact=True),
               f'"{label}" must be offered on the detail page').to_be_visible()

    copy = page.get_by_role('button', name='Copy to New')
    expect(copy, '"Copy to New" must be offered').to_be_visible()
    copy.click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_label('Name *'),
           'Copy to New must prefill the name with a -copied suffix'
          ).to_have_value(f'{name}-copied')
    expect(dialog.get_by_role('combobox'),
           'Copy to New must keep the recipe type').to_have_text('Managed Job')
    expect(dialog.locator('.cm-content'),
           'Copy to New must copy the YAML').to_contain_text(f'echo {uid}')
    dialog.get_by_role('button', name='Cancel').click()
    expect(dialog, 'Cancel must close the copy dialog').to_be_hidden()
    assert not page.recorder.posts(RECIPES_CREATE), (
        'Copy to New must not create anything until submitted')


@with_twists(
    Twist('dom-detail-delete-does-nothing',
          'dom',
          expect=r'Delete must ask for confirmation',
          apply=swallow_click('Delete')),
    Twist('req-non-author-deletes-other-recipe',
          'req',
          expect=r'confirming must send /recipes/delete for the recipe',
          apply=rewrite_request(
              RECIPES_DELETE, lambda b: {
                  **b, 'recipe_name': b.get('recipe_name', '') + '-gone'
              })),
    Twist(
        'src-recipes_settings_nav-delete-ignores-owner',
        'src',
        expect=(r'a non-author must not be able to delete the recipe'
                r'|a refused delete must say it failed'),
        patch='twist_patches/recipes_settings_nav-delete-ignores-owner.patch'),
)
def test_non_author_cannot_delete_recipe(make_user, open_as, recipes, uid,
                                         twist):
    author = make_user('author')
    other = make_user('other')
    name = recipes.seed(f'{uid}-theirs', author)

    page = open_as(other, f'/dashboard/recipes/{name}')
    expect(page.get_by_role('heading', name=name, level=1),
           'the detail page must load').to_be_visible()
    page.get_by_role('button', name='Delete', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(
        dialog.get_by_text(f'Are you sure you want to delete "{name}"?'),
        'Delete must ask for confirmation, naming the recipe').to_be_visible()
    since = page.recorder.mark()
    dialog.get_by_role('button', name='Delete').click()
    body = _wait_post(page, RECIPES_DELETE, since,
                      'confirming must send /recipes/delete')
    assert body.get('recipe_name') == name, (
        f'confirming must send /recipes/delete for the recipe {name}, '
        f'got {body}')
    expect(_toast(page, 'Delete failed: Failed to delete recipe'),
           'a refused delete must say it failed').to_be_visible()
    assert seed_recipes.get(name) is not None, (
        'a non-author must not be able to delete the recipe')
    # A refused delete leaves the confirmation open; the modal hides the page
    # from the accessibility tree until it is dismissed.
    dialog.get_by_role('button', name='Cancel').click()
    expect(dialog, 'Cancel must close the refused delete').to_be_hidden()
    expect(page.get_by_role('heading', name=name, level=1),
           'a refused delete must leave the user on the recipe page'
          ).to_be_visible()
