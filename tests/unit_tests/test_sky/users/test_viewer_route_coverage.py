"""Route coverage assertion for the viewer-role allowlist.

This test walks every registered FastAPI route on the API server's
`app` and confirms that the test author made a deliberate
allow-or-deny decision for each `(path, method)` pair.  The point is
to catch new endpoints added in future PRs that forget to opt into
the viewer allowlist (a useful read endpoint that silently 403s) OR
deliberately stay off it (a write endpoint correctly denied by
default).

Failure modes:
  * Test fails with "uncategorized" -> the PR added or renamed an
    endpoint without thinking about viewer. Decide: put it on
    `_DEFAULT_VIEWER_ALLOWLIST` (read) or in this file's
    `_KNOWN_VIEWER_DENIED` (write/sensitive).
  * Test fails with "allowlist has unknown entry" -> the
    `_DEFAULT_VIEWER_ALLOWLIST` references a path that no longer
    exists on the FastAPI app.  Update the constant.

The "known denied" set is the source of truth for endpoints that
SHOULD remain denied for viewer.  It is intentionally explicit
rather than catch-all so that the failure on a new endpoint
forces a deliberate review.
"""

from casbin import util as casbin_util
from fastapi.routing import APIRoute

from sky.server import server as server_app
from sky.users import rbac


def _fastapi_path_to_casbin(template: str) -> str:
    """Convert a FastAPI `{name}` path template to Casbin `:name`."""
    out = []
    i = 0
    while i < len(template):
        c = template[i]
        if c == '{':
            j = template.find('}', i)
            assert j > i, f'unterminated path placeholder in {template!r}'
            out.append(':' + template[i + 1:j])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


# Paths that, on the OSS app, are deliberately NOT on the viewer
# allowlist.  This is the source of truth for "write or sensitive
# read" classification — keep it in sync with the architecture
# map's §2.3 and §2.5.
#
# Each entry is (path_template_from_fastapi, method).
_KNOWN_VIEWER_DENIED: set = {
    # --- Cluster writes ---
    ('/launch', 'POST'),
    ('/exec', 'POST'),
    ('/start', 'POST'),
    ('/stop', 'POST'),
    ('/down', 'POST'),
    ('/autostop', 'POST'),
    ('/cancel', 'POST'),
    ('/local_up', 'POST'),
    ('/local_down', 'POST'),
    ('/upload', 'POST'),
    ('/upload_v2', 'POST'),
    ('/storage/delete', 'POST'),
    ('/kubernetes_label_gpus', 'POST'),
    ('/check', 'POST'),  # mutates state.db
    # --- Auth writes ---
    ('/api/v1/auth/authorize', 'POST'),
    ('/api/cancel', 'POST'),
    # Viewers are read-only; they cannot mutate their own preferred
    # workspace. (The matching GET is in rbac._DEFAULT_VIEWER_ALLOWLIST.)
    ('/users/me/workspace', 'POST'),
    # --- Managed jobs writes ---
    ('/jobs/launch', 'POST'),
    ('/jobs/cancel', 'POST'),
    ('/jobs/pool_apply', 'POST'),
    ('/jobs/pool_down', 'POST'),
    # --- Serve writes ---
    ('/serve/up', 'POST'),
    ('/serve/down', 'POST'),
    ('/serve/update', 'POST'),
    ('/serve/terminate-replica', 'POST'),
    # --- Volumes writes ---
    ('/volumes/apply', 'POST'),
    ('/volumes/delete', 'POST'),
    # --- Users writes (incl. SA token mgmt) ---
    ('/users/create', 'POST'),
    ('/users/update', 'POST'),
    ('/users/batch_update', 'POST'),
    ('/users/delete', 'POST'),
    ('/users/import', 'POST'),
    ('/users/export', 'GET'),  # password hashes
    ('/users/service-account-tokens', 'POST'),
    ('/users/service-account-tokens/delete', 'POST'),
    ('/users/service-account-tokens/update-role', 'POST'),
    ('/users/service-account-tokens/get-role', 'POST'),
    ('/users/service-account-tokens/rotate', 'POST'),
    # --- Workspaces writes (incl. sensitive config read) ---
    ('/workspaces/create', 'POST'),
    ('/workspaces/update', 'POST'),
    ('/workspaces/delete', 'POST'),
    ('/workspaces/batch_add_users', 'POST'),
    ('/workspaces/batch_remove_users', 'POST'),
    ('/workspaces/config', 'GET'),  # full admin config -> tokens, etc.
    ('/workspaces/config', 'POST'),
    # --- Recipes writes ---
    ('/recipes/create', 'POST'),
    ('/recipes/update', 'POST'),
    ('/recipes/delete', 'POST'),
    ('/recipes/pin', 'POST'),
    # --- SSH node pool writes ---
    ('/ssh_node_pools', 'POST'),
    ('/ssh_node_pools/{pool_name}', 'DELETE'),
    ('/ssh_node_pools/keys', 'POST'),
    ('/ssh_node_pools/keys', 'GET'),  # exposes private-key paths
    ('/ssh_node_pools/{pool_name}/deploy', 'POST'),
    ('/ssh_node_pools/deploy', 'POST'),
    ('/ssh_node_pools/{pool_name}/down', 'POST'),
    ('/ssh_node_pools/down', 'POST'),
    # --- Debug ---
    # /debug/dump_create is intentionally on the viewer allowlist (see
    # _DEFAULT_VIEWER_ALLOWLIST) so viewers can capture support diagnostics;
    # only the download endpoint stays admin-only because the dump file
    # itself may contain sensitive state.
    ('/debug/dump_download/{dump_filename}', 'GET'),
    # --- /api/* paths: RBAC-skipped at middleware level, not on
    # viewer allowlist; explicitly enumerated here for documentation.
    # These are reachable to viewer regardless of allowlist (the
    # middleware short-circuits before the role dispatch), but we
    # mark them "denied" in the *intent* sense so the coverage test
    # forces a deliberate decision.
    ('/api/get', 'GET'),
    ('/api/stream', 'GET'),
    ('/api/status', 'GET'),
    ('/api/health', 'GET'),
    ('/api/cancel', 'POST'),  # request cancellation
    ('/api/completion/cluster_name', 'GET'),
    ('/api/completion/storage_name', 'GET'),
    ('/api/completion/volume_name', 'GET'),
    ('/api/completion/api_request', 'GET'),
    # --- Misc index route ---
    ('/', 'GET'),
}

# Some FastAPI routes are HEAD/OPTIONS pairs of GET. The coverage
# test ignores these to focus on user-meaningful HTTP verbs.
_IGNORED_METHODS = {'HEAD', 'OPTIONS'}

# HTTP verbs that appear as keys in an OpenAPI Path Item Object. Used to
# distinguish operations from sibling keys like ``parameters``/``summary``.
_HTTP_METHODS = {
    'GET', 'PUT', 'POST', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH', 'TRACE'
}


def _iter_api_routes(routes, prefix=''):
    """Recursively yield (full_path, APIRoute) for every leaf route.

    FastAPI >=0.137 reworked router inclusion: ``app.routes`` is now a
    tree of private ``_IncludedRouter`` wrappers whose child routes and
    prefix live on ``include_context`` (rather than a flat list of
    fully-prefixed ``APIRoute``s).  We descend that structure defensively
    -- every access is a guarded ``getattr`` so the walk degrades to the
    routes it can reach rather than raising if FastAPI reshapes its
    internals again.  On older FastAPI the tree is already flat, so the
    first branch yields everything and the descent is never needed.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
            continue
        # FastAPI >=0.137: an included APIRouter is wrapped; its children
        # and prefix hang off the private include_context.
        ctx = getattr(route, 'include_context', None)
        sub_router = getattr(ctx, 'included_router', None)
        if sub_router is not None:
            yield from _iter_api_routes(
                sub_router.routes, prefix + (getattr(ctx, 'prefix', '') or ''))
            continue
        # Older FastAPI / Starlette mounts expose children directly.
        sub_routes = getattr(route, 'routes', None)
        if sub_routes:
            yield from _iter_api_routes(
                sub_routes, prefix + (getattr(route, 'path', '') or ''))


def _route_pairs():
    """Enumerate (path_template, method) for each app route.

    The visible surface is read from the public OpenAPI schema rather than
    by walking ``app.routes`` directly.  FastAPI >=0.137 reworked router
    inclusion so that ``app.routes`` is a nested tree of private
    ``_IncludedRouter`` objects instead of a flat list of fully-prefixed
    ``APIRoute``s; a sub-router included with ``prefix=`` no longer
    surfaces its routes at the top level, so the old flat scan silently
    dropped every prefixed endpoint (``/jobs/*``, ``/serve/*``,
    ``/users/*``, ...).  ``app.openapi()`` is a documented, version-stable
    API that flattens router prefixes into full ``{name}`` path templates
    -- exactly the form ``_fastapi_path_to_casbin`` expects -- and behaves
    identically on older FastAPI releases.  Keeping it as the primary
    source means the full security surface stays covered even if FastAPI
    reshapes its private routing internals yet again.
    """
    app = server_app.app
    seen = set()
    paths = app.openapi().get('paths') or {}
    for path, path_item in paths.items():
        for method in path_item:
            verb = method.upper()
            if verb in _HTTP_METHODS and verb not in _IGNORED_METHODS:
                seen.add((path, verb))
    # Safety net: routes registered with ``include_in_schema=False`` are
    # absent from the OpenAPI schema.  The app has none today, but this is
    # a security-coverage test -- a future hidden route must not silently
    # escape a viewer decision.  Recurse the router tree to catch hidden
    # routes at any nesting depth (a top-level-only scan would miss ones
    # added to a prefixed sub-router under FastAPI >=0.137).
    for full_path, route in _iter_api_routes(app.routes):
        if not route.include_in_schema:
            for method in route.methods or set():
                if method not in _IGNORED_METHODS:
                    seen.add((full_path, method))
    yield from seen


def _matches_any(path_template: str, method: str, allowlist: list) -> bool:
    casbin_path = _fastapi_path_to_casbin(path_template)
    for entry in allowlist:
        if entry['method'] != method:
            continue
        if casbin_util.key_match2(casbin_path, entry['path']):
            return True
    return False


def test_every_route_has_a_viewer_decision():
    """Every API route must be either viewer-allowed or viewer-denied.

    No silent "neither" state — if you add or rename a route you
    must update either `rbac._DEFAULT_VIEWER_ALLOWLIST` (it's a
    read) or this file's `_KNOWN_VIEWER_DENIED` (it's a write or a
    sensitive read).
    """
    allowlist = rbac._DEFAULT_VIEWER_ALLOWLIST  # pylint: disable=protected-access
    uncategorized = []
    for path_template, method in _route_pairs():
        on_allow = _matches_any(path_template, method, allowlist)
        on_deny = (path_template, method) in _KNOWN_VIEWER_DENIED
        if on_allow and on_deny:
            uncategorized.append(
                f'{method} {path_template} appears in BOTH lists; '
                'remove from one or the other.')
        elif not on_allow and not on_deny:
            uncategorized.append(
                f'{method} {path_template} has no viewer decision. '
                'Add to rbac._DEFAULT_VIEWER_ALLOWLIST (read) or to '
                'this file\'s _KNOWN_VIEWER_DENIED (write/sensitive).')
    assert not uncategorized, ('Routes with no viewer-role decision:\n  ' +
                               '\n  '.join(sorted(uncategorized)))


def test_allowlist_entries_match_real_routes():
    """Every allowlist entry must correspond to a real route.

    Forgotten entries (e.g. after an endpoint is renamed) would
    silently grant viewer access to a path that no longer exists,
    or worse, to a path that resembles a renamed admin endpoint.
    """
    pairs = list(_route_pairs())
    casbin_pairs = [(_fastapi_path_to_casbin(p), m) for p, m in pairs]
    allowlist = rbac._DEFAULT_VIEWER_ALLOWLIST  # pylint: disable=protected-access

    # Some allowlist patterns intentionally target endpoints not in
    # the OSS app's route table — these come from plugins or from
    # auth flows registered via the auth plugin.  Ignore those
    # known-out-of-tree entries for this test.
    out_of_tree = {
        # /api/plugins is registered at app construction time but
        # the auth-plugin endpoints are not present in the OSS app.
        # No entries in the OSS allowlist target the auth plugin
        # surface today, but keep the carve-out infrastructure here
        # so future operator overrides via config don't break this
        # test.
    }
    unknown = []
    for entry in allowlist:
        pattern_path = entry['path']
        method = entry['method']
        if (pattern_path, method) in out_of_tree:
            continue
        matched = False
        for actual_path, actual_method in casbin_pairs:
            if actual_method != method:
                continue
            if casbin_util.key_match2(actual_path, pattern_path):
                matched = True
                break
        if not matched:
            unknown.append(f'{method} {pattern_path}')
    assert not unknown, (
        'Viewer allowlist entries that do not match any registered '
        f'route: {unknown!r}.  If the endpoint was renamed, update '
        'rbac._DEFAULT_VIEWER_ALLOWLIST.')
