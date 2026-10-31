"""Infra-read endpoints 404 a context or cluster outside the caller's scope.

The handlers are called directly with a real ``auth_user`` (a loopback
TestClient request would have none, i.e. be unscoped) and stop at the
executor, which is mocked. The scope itself is tested in
``tests/unit_tests/test_sky/workspaces/test_visibility.py``.
"""
import asyncio
from unittest import mock

import fastapi
from fastapi.openapi import utils as openapi_utils
import pytest

from sky import models
from sky.server import server
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import rbac
from sky.workspaces import visibility

_ENV = {constants.USER_ID_ENV_VAR: 'x', constants.USER_ENV_VAR: 'x'}

_SCOPES = {
    'alice': visibility.InfraScope(kubernetes=frozenset({'ctx-a', 'gb300'}),
                                   slurm=frozenset({'sc-a'})),
    'bob': visibility.InfraScope(kubernetes=frozenset({'ctx-b'}),
                                 slurm=frozenset({'sc-b', 'gb300'})),
    'admin': visibility.UNSCOPED,
}


@pytest.fixture
def scheduled(monkeypatch):
    schedule = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', schedule)
    monkeypatch.setattr(
        visibility, 'scope_for', lambda user, *_, **__: _SCOPES[user.id]
        if user is not None else visibility.UNSCOPED)
    monkeypatch.setattr(visibility, 'current_kubernetes_context',
                        lambda: 'ctx-b')
    return schedule


def _request(user_id):
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    request.state.request_id = 'request-id'
    request.state.auth_user = (models.User(id=user_id, name=user_id)
                               if user_id else None)
    return request


def _call(handler, user_id, *args):
    """The status the handler answers with; 200 if it was let through."""
    try:
        asyncio.run(handler(_request(user_id), *args))
    except fastapi.HTTPException as e:
        assert isinstance(e.detail, dict), e.detail
        assert e.detail['error'] == 'infra_target_not_found'
        return e.status_code
    return 200


def _scheduled_body(schedule):
    schedule.assert_awaited_once()
    return schedule.await_args.kwargs['request_body']


def _node_info(context):
    return payloads.KubernetesNodeInfoRequestBody(context=context,
                                                  env_vars=_ENV)


@pytest.mark.parametrize('user_id,context,status', [
    ('alice', 'ctx-a', 200),
    ('alice', 'ctx-b', 404),
    ('alice', '', 404),
    ('alice', 'ctx-unknown', 404),
    ('admin', 'ctx-b', 200),
    (None, 'ctx-b', 200),
])
def test_kubernetes_node_info(scheduled, user_id, context, status):
    assert _call(server.kubernetes_node_info, user_id,
                 _node_info(context)) == status
    assert scheduled.await_count == (1 if status == 200 else 0)
    if status == 200:
        assert _scheduled_body(scheduled).context == context


def test_kubernetes_node_info_resolves_the_current_context(scheduled):
    # The server's current-context (ctx-b) is visible to bob: he gets it,
    # resolved here rather than left to the worker's fallback.
    assert _call(server.kubernetes_node_info, 'bob', _node_info(None)) == 200
    assert _scheduled_body(scheduled).context == 'ctx-b'


def test_kubernetes_node_info_hidden_current_context(scheduled):
    assert _call(server.kubernetes_node_info, 'alice', _node_info(None)) == 404
    scheduled.assert_not_awaited()


def test_kubernetes_node_info_unresolvable_current_context(
        scheduled, monkeypatch):
    monkeypatch.setattr(visibility, 'current_kubernetes_context', lambda: None)
    assert _call(server.kubernetes_node_info, 'alice', _node_info(None)) == 404
    scheduled.assert_not_awaited()


def test_admin_keeps_the_worker_fallback(scheduled):
    assert _call(server.kubernetes_node_info, 'admin', _node_info(None)) == 200
    assert _scheduled_body(scheduled).context is None


@pytest.mark.parametrize(
    'user_id,context,status',
    [
        ('alice', 'ctx-a', 200),
        ('alice', 'ctx-b', 404),
        ('alice', '', 404),
        # No context: already scoped to the active workspace by the worker.
        ('alice', None, 200),
        ('admin', 'ctx-b', 200),
    ])
def test_realtime_kubernetes_gpu_availability(scheduled, user_id, context,
                                              status):
    body = payloads.RealtimeGpuAvailabilityRequestBody(context=context,
                                                       env_vars=_ENV)
    assert _call(server.realtime_kubernetes_gpu_availability, user_id,
                 body) == status


@pytest.mark.parametrize('user_id,status', [('alice', 404), ('bob', 200),
                                            ('admin', 200)])
def test_status_kubernetes(scheduled, user_id, status):
    # It reads the server's current-context (ctx-b).
    assert _call(server.status_kubernetes, user_id) == status


@pytest.mark.parametrize('handler,body_cls', [
    (server.slurm_gpu_availability, payloads.SlurmGpuAvailabilityRequestBody),
    (server.slurm_node_info, payloads.SlurmNodeInfoRequestBody),
])
@pytest.mark.parametrize('user_id,cluster,status', [
    ('alice', 'sc-a', 200),
    ('alice', 'sc-b', 404),
    ('alice', '', 404),
    ('alice', None, 200),
    ('admin', 'sc-b', 200),
])
def test_slurm_endpoints(scheduled, handler, body_cls, user_id, cluster,
                         status):
    body = body_cls(slurm_cluster_name=cluster, env_vars=_ENV)
    assert _call(handler, user_id, body) == status


@pytest.mark.parametrize('handler,body_cls', [
    (server.list_accelerators, payloads.ListAcceleratorsBody),
    (server.list_accelerator_counts, payloads.ListAcceleratorCountsBody),
])
class TestAcceleratorListing:

    def _clouds(self, scheduled, handler, body_cls, user_id, clouds, region):
        body = body_cls(clouds=clouds, region_filter=region, env_vars=_ENV)
        status = _call(handler, user_id, body)
        if status != 200:
            scheduled.assert_not_awaited()
            return status
        return _scheduled_body(scheduled).clouds

    def test_infra_only_hidden_is_404(self, scheduled, handler, body_cls):
        assert self._clouds(scheduled, handler, body_cls, 'alice', 'kubernetes',
                            'ctx-b') == 404

    def test_infra_only_visible_is_unchanged(self, scheduled, handler,
                                             body_cls):
        assert self._clouds(scheduled, handler, body_cls, 'alice',
                            ['kubernetes'], 'ctx-a') == ['kubernetes']

    def test_kelvin_sign_is_classified_as_kubernetes(self, scheduled, handler,
                                                     body_cls):
        # 'K' (U+212A) lower-cases to 'k', so the catalog dispatches it to
        # kubernetes; the guard must see it the same way.
        assert self._clouds(scheduled, handler, body_cls, 'alice', 'Kubernetes',
                            'ctx-b') == 404
        scheduled.reset_mock()
        assert self._clouds(scheduled, handler, body_cls, 'alice',
                            ['Kubernetes', 'aws'], 'ctx-b') == ['aws']

    def test_mixed_drops_only_the_hidden_infra_clouds(self, scheduled, handler,
                                                      body_cls):
        clouds = self._clouds(scheduled, handler, body_cls, 'alice', None,
                              'ctx-b')
        assert {'kubernetes', 'ssh', 'slurm'}.isdisjoint(clouds)
        assert 'aws' in clouds and 'gcp' in clouds

    def test_a_cloud_region_still_reaches_its_cloud(self, scheduled, handler,
                                                    body_cls):
        clouds = self._clouds(scheduled, handler, body_cls, 'alice', None,
                              'us-east-1')
        assert 'aws' in clouds

    def test_region_is_checked_per_cloud(self, scheduled, handler, body_cls):
        # gb300 is a context alice may read and a Slurm cluster she may not;
        # for bob it is the other way round.
        both = ['kubernetes', 'slurm']
        assert self._clouds(scheduled, handler, body_cls, 'alice', both,
                            'gb300') == ['kubernetes']
        scheduled.reset_mock()
        assert self._clouds(scheduled, handler, body_cls, 'bob', both,
                            'gb300') == ['slurm']

    def test_admin_and_no_region_are_untouched(self, scheduled, handler,
                                               body_cls):
        assert self._clouds(scheduled, handler, body_cls, 'admin', 'kubernetes',
                            'ctx-b') == 'kubernetes'
        scheduled.reset_mock()
        assert self._clouds(scheduled, handler, body_cls, 'alice', None,
                            None) is None


def test_label_gpus_is_admin_only():
    blocklist = rbac.get_role_permissions()['user']['permissions']['blocklist']
    assert {'path': '/kubernetes_label_gpus', 'method': 'POST'} in blocklist


# Fields that name an infra target. Every route taking one must be guarded,
# or be listed below with the reason it needs no guard.
_TARGET_FIELDS = {
    'context', 'k8s_context', 'region_filter', 'slurm_cluster_name'
}
_GUARDED = {
    '/kubernetes_node_info',
    '/realtime_kubernetes_gpu_availability',
    '/list_accelerators',
    '/list_accelerator_counts',
    '/slurm_gpu_availability',
    '/slurm_node_info',
}
_UNGUARDED = {
    # Admin-only through the default user blocklist.
    '/kubernetes_label_gpus',
    # One boolean about the context's config: leaks at most a name.
    '/kubernetes/allowed_nodes',
    # SSH node pool management routes are tracked separately.
    '/ssh_node_pools/{pool_name}',
    '/ssh_node_pools/{pool_name}/deploy',
    '/ssh_node_pools/{pool_name}/down',
    '/ssh_node_pools/{pool_name}/status',
}


def test_every_infra_read_is_guarded(monkeypatch):
    # The OpenAPI schema lists every route, including those of included
    # routers, which newer FastAPI versions keep out of `app.routes`.
    # Routes of loaded plugins are theirs to guard, so only SkyPilot's own
    # endpoints are recorded while the schema is built.
    sky_paths = set()
    get_openapi_path = openapi_utils.get_openapi_path

    def record(*, route, **kwargs):
        if getattr(route.endpoint, '__module__', '').startswith('sky.'):
            sky_paths.add(route.path_format)
        return get_openapi_path(route=route, **kwargs)

    monkeypatch.setattr(openapi_utils, 'get_openapi_path', record)
    monkeypatch.setattr(server.app, 'openapi_schema', None)
    spec = server.app.openapi()
    schemas = spec.get('components', {}).get('schemas', {})
    taking_a_target = set()
    for path, operations in spec['paths'].items():
        if path not in sky_paths:
            continue
        for operation in operations.values():
            names = {p['name'] for p in operation.get('parameters', [])}
            body = operation.get('requestBody', {}).get('content', {})
            for media in body.values():
                ref = media.get('schema', {}).get('$ref', '')
                names |= set(
                    schemas.get(ref.rsplit('/', 1)[-1],
                                {}).get('properties', {}))
            # An SSH node pool is a target too; elsewhere `pool_name` is a
            # managed-jobs pool.
            ssh_pool = (path.startswith('/ssh_node_pools/') and
                        'pool_name' in names)
            if names & _TARGET_FIELDS or ssh_pool:
                taking_a_target.add(path)
    assert taking_a_target == _GUARDED | _UNGUARDED


def test_listing_without_infra_clouds_skips_the_scope(scheduled, monkeypatch):
    """A cloud region (e.g. `--infra aws/us-east-1`) costs no scope."""
    scope_for = mock.Mock(side_effect=AssertionError('scope computed'))
    monkeypatch.setattr(visibility, 'scope_for', scope_for)
    body = payloads.ListAcceleratorsBody(clouds='aws',
                                         region_filter='us-east-1',
                                         env_vars=_ENV)
    assert _call(server.list_accelerators, 'alice', body) == 200
    assert _scheduled_body(scheduled).clouds == 'aws'


def test_slurm_cluster_names_lists_every_readable_workspace(scheduled):
    """The dashboard's Slurm list uses the same rule as the guards."""
    assert _call(server.slurm_cluster_names, 'alice') == 200
    assert (scheduled.await_args.kwargs['func'] is
            visibility.slurm_cluster_names)


def test_slurm_cluster_names_unauthenticated_is_unscoped(scheduled):
    assert _call(server.slurm_cluster_names, None) == 200
    assert (scheduled.await_args.kwargs['func'] is
            server.slurm_utils.slurm_cluster_names)
