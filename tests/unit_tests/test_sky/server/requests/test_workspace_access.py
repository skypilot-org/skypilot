"""Tests for the workspace-access classification.

`workspace_access.for_current_request` decides whether a request needs `read`
or `write` access to the caller's *active* workspace. The level comes from the
dispatched endpoint, in two ordered rules: (0) an endpoint in
`rbac._ALWAYS_WRITE_ENDPOINTS` (the create endpoints) always needs `write`,
short-circuiting the declaration below (this is what
`test_always_write_overrides_read_declaration` asserts); (1) otherwise the
level comes from the read-only endpoint declaration that also backs the
`viewer` role, and anything not declared read-only needs `write`.

`TestEveryExecutorEndpointIsClassified` is the drift guard: it discovers every
endpoint that schedules a request through the executor and asserts the level
each one resolves to, so a newly added endpoint fails here until someone
decides what it needs.
"""
import ast
import pathlib
from typing import Dict, List, Set, Tuple
from unittest import mock

import pytest

import sky
from sky.server.requests import request_names
from sky.server.requests import workspace_access
from sky.users import permission
from sky.users import rbac
from sky.workspaces import constants as workspace_constants

READ = workspace_constants.WORKSPACE_ACTION_READ
WRITE = workspace_constants.WORKSPACE_ACTION_WRITE

# Routers mounted by `sky/server/server.py`, and the prefix each is mounted
# under. A new router needs an entry here for its endpoints to be covered by
# the drift guard below.
_ROOT_SERVER_MODULE = 'sky/server/server.py'


def _discover_server_modules(root: pathlib.Path) -> Dict[str, str]:
    """Derive {module path -> route prefix} from `server.py`'s routers.

    Parses the ``app.include_router(<alias>.router, prefix='/x')`` calls in
    ``sky/server/server.py`` (plus its own root routes at prefix '') and
    resolves each router alias back to the module file it was imported from.

    Hand-maintaining this map (the previous approach) silently missed any new
    router that wasn't added here, so the drift guard wouldn't scan it -- the
    exact case it exists to catch. Deriving it from ``include_router`` means a
    newly mounted router is scanned automatically.
    """
    server_py = root / _ROOT_SERVER_MODULE
    if not server_py.exists():
        pytest.skip(f'source tree not available: {_ROOT_SERVER_MODULE}')
    tree = ast.parse(server_py.read_text())

    # alias -> dotted module (e.g. 'jobs_rest' -> 'sky.jobs.server.server')
    alias_to_module: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            if alias.asname:
                alias_to_module[alias.asname] = f'{node.module}.{alias.name}'

    modules: Dict[str, str] = {_ROOT_SERVER_MODULE: ''}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and
                func.attr == 'include_router'):
            continue
        # First positional arg is expected to be `<alias>.router`. Anything
        # else (a bare Name, a nested Attribute, a Call) means the drift guard
        # cannot statically locate the module to scan -- fail loudly rather
        # than silently skip it, which is the exact blind spot this guard
        # exists to close.
        if not (node.args and isinstance(node.args[0], ast.Attribute) and
                isinstance(node.args[0].value, ast.Name)):
            raise AssertionError(
                'drift guard cannot locate the module for an '
                '`include_router(...)` call whose first argument is not '
                '`<alias>.router`; rewrite it to that form or extend '
                '`_discover_server_modules` to resolve it.')
        alias = node.args[0].value.id
        prefix = ''
        for kw in node.keywords:
            if kw.arg == 'prefix' and isinstance(kw.value, ast.Constant):
                prefix = kw.value.value
        dotted = alias_to_module.get(alias)
        if dotted is None:
            # A router mounted from an alias we couldn't resolve to a module:
            # fail loudly rather than silently skipping it.
            raise AssertionError(
                f'include_router uses alias {alias!r} that maps to no '
                f'`from ... import ... as {alias}` import; the drift guard '
                f'cannot locate its module to scan.')
        modules[dotted.replace('.', '/') + '.py'] = prefix
    return modules


_SCHEDULERS = ('schedule_request_async', 'schedule_request',
               'prepare_request_async')
_HTTP_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'PATCH')

# The access level every executor-backed endpoint needs on the caller's active
# workspace. `write` is the fallback for anything not declared read-only, so
# entries here are a statement of intent, not the enforcement itself.
#
# Path templates (`{pool_name}`) are matched against the declaration's casbin
# patterns (`:pool_name`) the same way a concrete path segment would be, so
# they classify identically to a real request.
_EXPECTED: Dict[Tuple[str, str], str] = {
    # --- read: state-only endpoints, declared read-only for the viewer role
    ('GET', '/all_contexts'): READ,
    # Cancelling a request touches the request queue, not a workspace; see
    # rbac._WORKSPACE_READ_EXTRA_ENDPOINTS.
    ('POST', '/api/cancel'): READ,
    ('POST', '/cluster_events'): READ,
    ('POST', '/cost_report'): READ,
    ('POST', '/download_logs'): READ,
    ('GET', '/enabled_clouds'): READ,
    ('GET', '/enabled_clouds/batch'): READ,
    ('POST', '/endpoints'): READ,
    ('POST', '/hook_logs'): READ,
    ('POST', '/job_status'): READ,
    ('POST', '/jobs/download_logs'): READ,
    ('POST', '/jobs/events'): READ,
    ('POST', '/jobs/logs'): READ,
    ('POST', '/jobs/pool_logs'): READ,
    ('POST', '/jobs/pool_status'): READ,
    ('POST', '/jobs/pool_sync-down-logs'): READ,
    ('POST', '/jobs/queue'): READ,
    ('POST', '/jobs/queue/v2'): READ,
    ('POST', '/jobs/wait'): READ,
    ('POST', '/kubernetes_node_info'): READ,
    ('POST', '/list_accelerator_counts'): READ,
    ('POST', '/list_accelerators'): READ,
    ('POST', '/logs'): READ,
    ('POST', '/optimize'): READ,
    ('POST', '/queue'): READ,
    ('POST', '/realtime_kubernetes_gpu_availability'): READ,
    ('GET', '/recipes'): READ,
    ('POST', '/recipes/get'): READ,
    ('POST', '/recipes/list'): READ,
    ('POST', '/serve/logs'): READ,
    ('POST', '/serve/status'): READ,
    ('POST', '/serve/sync-down-logs'): READ,
    ('POST', '/slurm_cluster_names'): READ,
    ('POST', '/slurm_gpu_availability'): READ,
    ('GET', '/slurm_node_info'): READ,
    ('POST', '/slurm_node_info'): READ,
    ('POST', '/status'): READ,
    ('GET', '/status_kubernetes'): READ,
    ('GET', '/storage/ls'): READ,
    ('GET', '/volumes'): READ,
    # The executor skips the workspace gate for this one entirely (it is how a
    # client discovers which workspaces it may use), so the level is moot.
    ('GET', '/workspaces'): READ,

    # --- write: creates new compute. For launch / jobs launch / pool apply the
    # workload lands in the caller's active workspace; serve up/update pull up
    # new replicas the same way (the serve *controller* is pinned to the default
    # workspace, so a service is not "stamped" with the active workspace, but it
    # still creates compute, so write is the conservative and correct level).
    # volumes/apply is currently workspace-agnostic; it is here so a read-only
    # user cannot provision storage. All of these are in
    # `rbac._ALWAYS_WRITE_ENDPOINTS`, so they stay write even if a viewer
    # allowlist entry (e.g. a `/serve/*` wildcard) would otherwise match.
    ('POST', '/launch'): WRITE,
    ('POST', '/jobs/launch'): WRITE,
    ('POST', '/volumes/apply'): WRITE,
    ('POST', '/serve/up'): WRITE,
    ('POST', '/serve/update'): WRITE,
    ('POST', '/jobs/pool_apply'): WRITE,

    # --- write: mutates an existing resource. For clusters and managed jobs
    # the load-bearing check is on the *target's* own workspace (clusters via
    # workspaces_core.check_cluster_write_permission at the handler; jobs cancel
    # via the workspace comparison in cancel_jobs_by_id), so classifying them
    # write here is belt-and-suspenders. serve down / terminate-replica, pool
    # down, and volume/storage delete have NO per-resource workspace gate yet
    # (the gap recorded in docs/source/admin/workspaces.rst); write here only
    # keeps a read-only user out, it is not their real protection.
    ('POST', '/exec'): WRITE,
    ('POST', '/stop'): WRITE,
    ('POST', '/start'): WRITE,
    ('POST', '/down'): WRITE,
    ('POST', '/autostop'): WRITE,
    ('POST', '/cancel'): WRITE,
    ('POST', '/jobs/cancel'): WRITE,
    ('POST', '/jobs/pool_down'): WRITE,
    ('POST', '/serve/down'): WRITE,
    ('POST', '/serve/terminate-replica'): WRITE,
    ('POST', '/volumes/delete'): WRITE,
    ('POST', '/storage/delete'): WRITE,

    # --- write: no workspace dimension at all (infra / config / catalog).
    # These are gated by role, not by workspace; `write` is simply the
    # fallback and costs nothing, since the users entitled to call them are
    # members or admins.
    # /debug/dump_create is admin-only (removed from the viewer allowlist by
    # the request-endpoint scoping fix), so it is not viewer-read -> write.
    ('POST', '/debug/dump_create'): WRITE,
    ('POST', '/check'): WRITE,
    ('POST', '/kubernetes_label_gpus'): WRITE,
    ('POST', '/local_up'): WRITE,
    ('POST', '/local_down'): WRITE,
    ('POST', '/ssh_node_pools/deploy'): WRITE,
    ('POST', '/ssh_node_pools/down'): WRITE,
    ('POST', '/ssh_node_pools/{pool_name}/deploy'): WRITE,
    ('POST', '/ssh_node_pools/{pool_name}/down'): WRITE,
    ('POST', '/recipes/create'): WRITE,
    ('POST', '/recipes/update'): WRITE,
    ('POST', '/recipes/delete'): WRITE,
    ('POST', '/recipes/pin'): WRITE,
    ('POST', '/workspaces/create'): WRITE,
    ('POST', '/workspaces/update'): WRITE,
    ('POST', '/workspaces/delete'): WRITE,
    ('POST', '/workspaces/config'): WRITE,
    ('POST', '/workspaces/batch_add_users'): WRITE,
    ('POST', '/workspaces/batch_remove_users'): WRITE,
    # Deliberately NOT read: the payload includes admin-only secrets, which is
    # why `rbac` keeps it off the viewer allowlist.
    ('GET', '/workspaces/config'): WRITE,
}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(sky.__file__).parent.parent


def _route_decorators(node: ast.AST) -> List[Tuple[str, str]]:
    """(method, path) for each route decorator on a function definition."""
    routes: List[Tuple[str, str]] = []
    for decorator in getattr(node, 'decorator_list', []):
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute):
            continue
        if not decorator.args or not isinstance(decorator.args[0],
                                                ast.Constant):
            continue
        path = decorator.args[0].value
        attr = func.attr.upper()
        if attr == 'API_ROUTE':
            for keyword in decorator.keywords:
                if keyword.arg == 'methods' and isinstance(
                        keyword.value, (ast.List, ast.Tuple)):
                    for element in keyword.value.elts:
                        if isinstance(element, ast.Constant):
                            routes.append((element.value, path))
        elif attr in _HTTP_METHODS:
            routes.append((attr, path))
    return routes


def _scheduled_request_names(node: ast.AST) -> Set[str]:
    """RequestName attribute names scheduled from within a handler."""
    names: Set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(
            func, 'id', '')
        if called not in _SCHEDULERS:
            continue
        for keyword in sub.keywords:
            if keyword.arg != 'request_name':
                continue
            source = ast.unparse(keyword.value)
            if 'RequestName.' in source:
                names.add(source.rsplit('.', 1)[1])
    return names


def _executor_endpoints() -> List[Tuple[str, str, str]]:
    """Discover (method, path, request name) for executor-backed endpoints."""
    root = _repo_root()
    found: List[Tuple[str, str, str]] = []
    for relative, prefix in _discover_server_modules(root).items():
        source = root / relative
        if not source.exists():
            pytest.skip(f'source tree not available: {relative}')
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes = _route_decorators(node)
            if not routes:
                continue
            for attr_name in sorted(_scheduled_request_names(node)):
                request_name = getattr(request_names.RequestName, attr_name)
                for method, path in routes:
                    found.append((method, prefix + path, request_name.value))
    return sorted(set(found))


@pytest.fixture(autouse=True)
def clear_endpoint():
    """No endpoint recorded unless a test sets one."""
    token = workspace_access._request_endpoint.set(None)  # pylint: disable=protected-access
    yield
    workspace_access._request_endpoint.reset(token)  # pylint: disable=protected-access


@pytest.fixture
def declaration(monkeypatch):
    """Wire the real read-only declaration in, without a DB or plugins.

    Neutralises the operator-supplied part (`rbac.roles.viewer.permissions
    .allowlist`) so a developer's own `~/.sky/config.yaml` cannot change the
    outcome.
    """

    def install(plugin_allowlist=None):
        monkeypatch.setattr(rbac.skypilot_config,
                            'get_nested',
                            lambda keys, default_value=None: default_value)
        entries = [(rule['path'], rule['method'])
                   for rule in rbac.get_read_only_endpoints(
                       plugin_allowlist=plugin_allowlist)]
        monkeypatch.setattr(
            permission.permission_service, 'is_read_only_endpoint',
            lambda path, method: permission.PermissionService._matches_endpoint(
                entries, path, method))
        return entries

    return install


class TestForCurrentRequest:
    """The classification itself: one rule, derived from the endpoint."""

    def test_declared_read_only_endpoint_needs_read(self, declaration):
        declaration()
        workspace_access.set_request_endpoint('/status', 'POST')
        assert workspace_access.for_current_request() == READ

    def test_undeclared_endpoint_needs_write(self, declaration):
        declaration()
        workspace_access.set_request_endpoint('/down', 'POST')
        assert workspace_access.for_current_request() == WRITE

    def test_method_must_match_the_declaration(self, declaration):
        declaration()
        # `/status` is declared for POST only.
        workspace_access.set_request_endpoint('/status', 'DELETE')
        assert workspace_access.for_current_request() == WRITE

    @pytest.mark.parametrize('method,path', [
        ('POST', '/launch'),
        ('POST', '/jobs/launch'),
        ('POST', '/volumes/apply'),
        ('POST', '/serve/up'),
        ('POST', '/serve/update'),
        ('POST', '/jobs/pool_apply'),
    ])
    def test_resource_creating_endpoints_need_write(self, declaration, method,
                                                    path):
        """These stamp the active workspace onto a new resource.

        They get `write` from the same fallback as everything else — none of
        them is declared read-only. Kept as an explicit test so the intent is
        stated somewhere other than the drift-guard table.
        """
        declaration()
        workspace_access.set_request_endpoint(path, method)
        assert workspace_access.for_current_request() == WRITE

    def test_no_endpoint_recorded_falls_back_to_write(self, declaration):
        """Daemon ticks and direct callers have no endpoint; fail safe."""
        declaration()
        assert workspace_access.get_request_endpoint() is None
        assert workspace_access.for_current_request() == WRITE

    def test_classification_failure_falls_back_to_write(self, monkeypatch):

        def boom(path, method):
            del path, method
            raise RuntimeError('permission service unavailable')

        monkeypatch.setattr(permission.permission_service,
                            'is_read_only_endpoint', boom)
        workspace_access.set_request_endpoint('/status', 'POST')
        assert workspace_access.for_current_request() == WRITE


class TestPluginEndpointsAreCovered:
    """The reason the level is derived from the endpoint, not the name.

    Plugin handlers schedule through this same executor with request names OSS
    has never heard of, so a list of OSS request names classifies every plugin
    read as a write — which denies it to exactly the users this feature is for
    (those whose only accessible workspace is read-only). Deriving from
    `BasePlugin.viewer_allowlist` covers them with no OSS change.
    """

    def test_plugin_read_endpoint_needs_only_read(self, declaration):
        declaration(plugin_allowlist=[{
            'path': '/plugins/api/endpoints/status',
            'method': 'POST'
        }])
        workspace_access.set_request_endpoint('/plugins/api/endpoints/status',
                                              'POST')
        # The request name (`endpoints.status`) is not in `RequestName` at
        # all; only the endpoint matters.
        assert workspace_access.for_current_request() == READ

    def test_undeclared_plugin_endpoint_needs_write(self, declaration):
        declaration(plugin_allowlist=[{
            'path': '/plugins/api/endpoints/status',
            'method': 'POST'
        }])
        workspace_access.set_request_endpoint('/plugins/api/endpoints/up',
                                              'POST')
        assert workspace_access.for_current_request() == WRITE

    def test_plugin_wildcard_declaration(self, declaration):
        declaration(plugin_allowlist=[{
            'path': '/plugins/api/kueue/*',
            'method': 'POST'
        }])
        workspace_access.set_request_endpoint('/plugins/api/kueue/queues',
                                              'POST')
        assert workspace_access.for_current_request() == READ


class TestReadOnlyEndpointDeclaration:
    """`rbac.get_read_only_endpoints` / `is_read_only_endpoint`."""

    def test_superset_of_the_viewer_allowlist(self):
        viewer = rbac.get_viewer_allowlist()
        read_only = rbac.get_read_only_endpoints()
        for entry in viewer:
            assert entry in read_only

    def test_includes_the_extra_endpoints(self):
        assert {
            'path': '/api/cancel',
            'method': 'POST'
        } in rbac.get_read_only_endpoints()

    def test_extra_endpoints_stay_out_of_the_viewer_allowlist(self):
        """The supplement is for workspace access only, not for the role."""
        assert {
            'path': '/api/cancel',
            'method': 'POST'
        } not in rbac.get_viewer_allowlist()

    def test_plugin_entries_are_merged(self):
        read_only = rbac.get_read_only_endpoints(plugin_allowlist=[{
            'path': '/plugins/api/foo/list',
            'method': 'GET'
        }])
        assert {'path': '/plugins/api/foo/list', 'method': 'GET'} in read_only

    @pytest.mark.parametrize('path,method,expected', [
        ('/status', 'POST', True),
        ('/status', 'GET', False),
        ('/launch', 'POST', False),
        ('/dashboard/clusters', 'GET', True),
        ('/ssh_node_pools/mypool/status', 'GET', True),
        ('/ssh_node_pools/mypool/keys', 'GET', False),
    ])
    def test_matcher(self, path, method, expected):
        service = permission.PermissionService()
        service._read_only_endpoints = [  # pylint: disable=protected-access
            (rule['path'], rule['method'])
            for rule in rbac.get_read_only_endpoints()
        ]
        with mock.patch.object(service, '_lazy_initialize'):
            assert service.is_read_only_endpoint(path, method) is expected

    @pytest.mark.parametrize(
        'path, method, expected',
        [
            # An always-write (create) endpoint stays write even when it is
            # (mis)declared read-only -- exact entry or a covering wildcard.
            ('/serve/up', 'POST', False),
            ('/launch', 'POST', False),
            ('/jobs/launch', 'POST', False),
            ('/jobs/pool_apply', 'POST', False),
            ('/volumes/apply', 'POST', False),
            # A sibling read under the same wildcard is still read.
            ('/serve/status', 'POST', True),
        ])
    def test_always_write_overrides_read_declaration(self, path, method,
                                                     expected):
        service = permission.PermissionService()
        # A read declaration that wrongly includes the create endpoints, plus a
        # `/serve/*` wildcard that would otherwise relax `/serve/up` too.
        service._read_only_endpoints = [  # pylint: disable=protected-access
            ('/serve/*', 'POST'),
            ('/launch', 'POST'),
            ('/jobs/launch', 'POST'),
            ('/jobs/pool_apply', 'POST'),
            ('/volumes/apply', 'POST'),
        ]
        with mock.patch.object(service, '_lazy_initialize'):
            assert service.is_read_only_endpoint(path, method) is expected


class TestEveryExecutorEndpointIsClassified:
    """Drift guard.

    Discovers every endpoint that schedules a request through the executor and
    checks the level it resolves to against `_EXPECTED`. A new endpoint fails
    here with a message telling the author what to decide; a change to the
    read-only declaration that moves an existing endpoint also fails.
    """

    def test_discovery_found_the_endpoints(self):
        endpoints = _executor_endpoints()
        # Sanity floor: if the scan silently stops matching decorators, the
        # rest of this class would vacuously pass.
        assert len(endpoints) > 60
        assert ('POST', '/launch',
                request_names.RequestName.CLUSTER_LAUNCH.value) in endpoints

    def test_no_endpoint_is_unclassified(self):
        missing = sorted((method, path, name)
                         for method, path, name in _executor_endpoints()
                         if (method, path) not in _EXPECTED)
        assert not missing, (
            'New executor-backed endpoint(s) with no expected workspace '
            f'access level: {missing}. Decide whether each one needs write '
            'access to the caller\'s active workspace (it creates a resource '
            'stamped with it) or only read (it just looks at state), add it '
            'to _EXPECTED, and — for a read — declare the endpoint in the '
            'viewer allowlist so the derivation returns read.')

    def test_expected_table_has_no_stale_entries(self):
        live = {(method, path) for method, path, _ in _executor_endpoints()}
        stale = sorted(key for key in _EXPECTED if key not in live)
        assert not stale, (f'_EXPECTED lists endpoints that no longer '
                           f'schedule a request: {stale}')

    def test_levels_match(self, declaration):
        declaration()
        wrong = []
        for method, path, name in _executor_endpoints():
            expected = _EXPECTED.get((method, path))
            if expected is None:
                continue
            workspace_access.set_request_endpoint(path, method)
            actual = workspace_access.for_current_request()
            if actual != expected:
                wrong.append((method, path, name, expected, actual))
        assert not wrong, ('workspace access level changed for '
                           f'(method, path, request, expected, actual): '
                           f'{wrong}')
