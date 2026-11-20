"""The first-visit tour survives asynchronous app-provider registration."""

from framework.fixtures import PROXY_HEADERS
from framework.twists import swallow_click
from framework.twists import Twist
from framework.twists import with_twists
from playwright.sync_api import expect
import pytest

_PROVIDER_PATH = '/plugins/e2e-tour-provider.js'
_PROVIDER_BUNDLE = """
(() => {
  const React = window.React;
  window.SkyDashboardPluginAPI.registerComponent({
    id: 'e2e-tour-provider',
    slot: 'app.providers',
    component: ({children}) => React.createElement(
      'div', {'data-testid': 'tour-test-provider'}, children),
  });
})();
"""


@pytest.mark.parametrize('late_provider', [False, True], ids=['early', 'late'])
@with_twists(
    Twist('dom-tour-skip-inactive',
          'dom',
          expect=r'Skip Tour must dismiss the welcome',
          apply=swallow_click('Skip Tour')))
def test_first_visit_tour_survives_provider_registration(
        make_user, browser, e2e_base_url, late_provider, twist):
    # Use a fresh context: open_as dismisses the tour in its init script.
    ident = make_user('tour-user')
    context = browser.new_context(base_url=e2e_base_url,
                                  extra_http_headers={
                                      'Authorization': f'Bearer {ident.token}',
                                      **PROXY_HEADERS,
                                  },
                                  viewport={
                                      'width': 1440,
                                      'height': 900
                                  })
    pending_routes = []
    release_provider = not late_provider

    def fulfill_provider(route):
        route.fulfill(content_type='application/javascript',
                      body=_PROVIDER_BUNDLE)

    def provider_request(route):
        if release_provider:
            fulfill_provider(route)
        else:
            # Keep the script pending while the browser runs the real bootstrap
            # timeout and shows the tour. Release it only after that assertion.
            pending_routes.append(route)

    context.route(
        '**/api/plugins', lambda route: route.fulfill(
            json={
                'plugins': [{
                    'name': 'TourTestProvider',
                    'js_extension_path': _PROVIDER_PATH,
                }]
            }))
    context.route(f'**{_PROVIDER_PATH}', provider_request)
    if twist is not None and twist.apply is not None:
        twist.apply(context)

    try:
        page = context.new_page()
        page.goto('/dashboard/clusters', wait_until='domcontentloaded')
        welcome = page.get_by_text('Welcome to SkyPilot!')
        expect(welcome,
               'a first visit must start the tour').to_be_visible(timeout=15000)

        if late_provider:
            assert pending_routes, 'the provider bundle must still be pending'
            release_provider = True
            for route in pending_routes:
                fulfill_provider(route)
            pending_routes.clear()

        expect(
            page.get_by_test_id('tour-test-provider'),
            'the provider must mount before checking the tour').to_be_visible()
        expect(welcome, 'registering a provider must preserve the active tour'
              ).to_be_visible()

        page.get_by_role('button', name='Skip Tour', exact=True).click()
        expect(welcome, 'Skip Tour must dismiss the welcome').to_be_hidden()
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_test_id('tour-test-provider')).to_be_visible()
        expect(page.get_by_role('button', name='Start Tour',
                                exact=True)).to_be_visible()
        expect(
            welcome,
            'a skipped tour must stay dismissed after reload').to_be_hidden()
    finally:
        for route in pending_routes:
            route.abort()
        context.close()
