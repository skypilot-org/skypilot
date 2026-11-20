"""Volumes are deleted, mounted and created only within the caller's reach.

Deleting or naming an existing volume is gated by the volume's own workspace;
creating one by the infra the active workspace can launch on.
"""
import asyncio
import contextlib
import textwrap
from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest

import sky
from sky import admin_policy
from sky import clouds
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import models
from sky import provision
from sky import skypilot_config
from sky.server import server as api_server
from sky.server.requests import executor
from sky.server.requests import request_names
from sky.users import permission
from sky.utils import admin_policy_utils
from sky.volumes.server import core
from sky.volumes.server import server
from sky.workspaces import core as workspaces_core

_ALICE = models.User(id='alice-id', name='alice')
_Requests = request_names.AdminPolicyRequestName


def _volume_record(workspace):
    return {
        'name': 'vol',
        'workspace': workspace,
        'handle': models.VolumeConfig(name='vol',
                                      type='k8s-pvc',
                                      cloud='kubernetes',
                                      region='ctx1',
                                      zone=None,
                                      size='1',
                                      config={},
                                      name_on_cloud='vol-abc'),
    }


# --- check_volume_write_permission ------------------------------------------


@pytest.mark.parametrize('stored, checked', [('ws-b', 'ws-b'),
                                             (None, 'default')])
@pytest.mark.parametrize('allowed', [True, False])
def test_volume_write_permission_checks_the_volumes_own_workspace(
        monkeypatch, stored, checked, allowed):
    monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                        lambda name: _volume_record(stored))
    check = mock.MagicMock(return_value=allowed)
    monkeypatch.setattr(permission.permission_service,
                        'check_workspace_permission', check)
    if allowed:
        workspaces_core.check_volume_write_permission(_ALICE, 'vol')
    else:
        with pytest.raises(exceptions.PermissionDeniedError, match="'vol'"):
            workspaces_core.check_volume_write_permission(_ALICE, 'vol')
    check.assert_called_once_with('alice-id', checked, action='write')


def test_volume_write_permission_leaves_a_missing_volume_to_the_caller(
        monkeypatch):
    monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                        lambda name: None)
    check = mock.MagicMock(return_value=False)
    monkeypatch.setattr(permission.permission_service,
                        'check_workspace_permission', check)
    workspaces_core.check_volume_write_permission(_ALICE, 'gone')
    check.assert_not_called()


# --- the mount gate at the entry of admin_policy_utils.apply ----------------


@pytest.fixture
def denied_volumes(monkeypatch):
    """Volumes the current user cannot write to; records every check."""
    denied = {'theirs'}
    checked = []

    def check(user, name):
        checked.append(name)
        if name in denied:
            raise exceptions.PermissionDeniedError(f'no access to {name}')

    monkeypatch.setattr(workspaces_core, 'check_volume_write_permission', check)
    monkeypatch.setattr(admin_policy_utils.common_utils, 'get_current_user',
                        lambda: _ALICE)
    return checked


def _task(volumes):
    task = sky.Task(run='echo hi')
    task.update_volumes(volumes)
    return task


_EXEMPT = {
    _Requests.VALIDATE,
    _Requests.JOBS_LAUNCH_CONTROLLER,
    _Requests.JOBS_LAUNCH_CLUSTER,
    _Requests.SERVE_LAUNCH_CONTROLLER,
    _Requests.SERVE_LAUNCH_REPLICA,
    _Requests.CLUSTER_EXEC,
}


@pytest.mark.parametrize('request_name', list(_Requests))
def test_every_request_naming_a_volume_is_gated_unless_exempt(
        denied_volumes, request_name):
    """No admin policy configured: the common deployment."""
    task = _task({'/data': 'theirs'})
    if request_name in _EXEMPT:
        admin_policy_utils.apply(task, request_name)
        assert not denied_volumes
    else:
        with pytest.raises(exceptions.PermissionDeniedError):
            admin_policy_utils.apply(task, request_name)


@pytest.mark.parametrize('volumes, checked', [
    ({
        '/a': 'mine'
    }, ['mine']),
    ({
        '/a': {
            'name': 'mine',
            'sub_path': 'x'
        }
    }, ['mine']),
    ({
        '/a': {
            'name': 'theirs',
            'size': '10Gi'
        }
    }, []),
    ({
        '/a': {
            'host_path': '/mnt/x'
        }
    }, []),
])
def test_only_named_existing_volumes_are_checked(denied_volumes, volumes,
                                                 checked):
    """A dict with `size` is an ephemeral volume even when it has a name, as
    in resolve_and_validate_volumes."""
    admin_policy_utils.apply(_task(volumes), _Requests.CLUSTER_LAUNCH)
    assert denied_volumes == checked


def test_preresolved_mounts_and_client_side_are_not_checked(denied_volumes):
    task = _task({'/data': 'theirs'})
    task.volume_mounts = []
    admin_policy_utils.apply(task, _Requests.CLUSTER_LAUNCH)
    admin_policy_utils.apply(_task({'/data': 'theirs'}),
                             _Requests.CLUSTER_LAUNCH,
                             at_client_side=True)
    assert not denied_volumes


class _AddsTheirVolume(admin_policy.AdminPolicy):

    @classmethod
    def validate_and_mutate(cls, user_request):
        user_request.task.update_volumes({'/shared': 'theirs'})
        return admin_policy.MutatedUserRequest(user_request.task,
                                               user_request.skypilot_config)


def test_a_volume_added_by_the_policy_is_not_checked(monkeypatch,
                                                     denied_volumes):
    monkeypatch.setattr(admin_policy_utils, '_get_policy_impl',
                        lambda location: _AddsTheirVolume())
    dag, _ = admin_policy_utils.apply(_task({'/data': 'mine'}),
                                      _Requests.CLUSTER_LAUNCH)
    assert dag.tasks[0].volumes['/shared'] == 'theirs'
    assert denied_volumes == ['mine']


# --- docker cache volumes the requester set ---------------------------------


@pytest.fixture
def server_docker_config(monkeypatch, tmp_path):
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    monkeypatch.delenv(skypilot_config.ENV_VAR_GLOBAL_CONFIG, raising=False)
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        textwrap.dedent("""\
            kubernetes:
              enable_docker:
                mode: BUILD
                cache_volume: admin-vol
              context_configs:
                ctx1:
                  enable_docker:
                    mode: BUILD
                    cache_volume: admin-ctx-vol
            """))
    monkeypatch.setattr(skypilot_config, '_GLOBAL_CONFIG_PATH', config_path)
    skypilot_config.reload_config()
    yield
    skypilot_config.reload_config()


def _docker(cache_volume):
    return {'enable_docker': {'mode': 'BUILD', 'cache_volume': cache_volume}}


@pytest.mark.parametrize('client, expected', [
    ({}, set()),
    ({
        'kubernetes': _docker('admin-vol')
    }, set()),
    ({
        'kubernetes': _docker('theirs')
    }, {'theirs'}),
    ({
        'kubernetes': {
            'context_configs': {
                'ctx1': _docker('theirs')
            }
        }
    }, {'theirs'}),
    ({
        'kubernetes': {
            'context_configs': {
                'ctx2': _docker('theirs')
            }
        }
    }, {'theirs'}),
    ({
        'kubernetes': {
            'context_configs': {
                'ctx1': _docker('admin-ctx-vol')
            }
        }
    }, set()),
])
def test_cache_volumes_from_the_client_config_are_user_named(
        server_docker_config, client, expected):
    with skypilot_config.override_skypilot_config(client, from_loopback=False):
        assert admin_policy_utils._user_set_cache_volumes(
            sky.Task(run='echo hi')) == expected


def test_cache_volume_from_a_task_override_is_user_named(server_docker_config):
    task = sky.Task(run='echo hi')
    task.set_resources(
        sky.Resources(infra='k8s',
                      _cluster_config_overrides={'kubernetes': _docker('vb')}))
    with skypilot_config.override_skypilot_config({}, from_loopback=False):
        assert admin_policy_utils._user_set_cache_volumes(task) == {'vb'}


@pytest.mark.parametrize('preresolved', [False, True])
def test_apply_gates_a_user_set_cache_volume(server_docker_config,
                                             denied_volumes, preresolved):
    """Read from config at provision time, so pre-resolved mounts do not
    skip it."""
    task = sky.Task(run='echo hi')
    if preresolved:
        task.volume_mounts = []
    with skypilot_config.override_skypilot_config(
        {'kubernetes': _docker('theirs')}, from_loopback=False):
        with pytest.raises(exceptions.PermissionDeniedError):
            admin_policy_utils.apply(task, _Requests.CLUSTER_LAUNCH)
    with skypilot_config.override_skypilot_config(
        {'kubernetes': _docker('admin-vol')}, from_loopback=False):
        admin_policy_utils.apply(task, _Requests.CLUSTER_LAUNCH)


@pytest.mark.parametrize('context, expected', [('ctx1', {'theirs'}),
                                               ('ctx2', set())])
def test_cache_volume_is_checked_only_on_a_pinned_context(
        server_docker_config, context, expected):
    """A task pinned to ctx1 never mounts what another context configures."""
    task = sky.Task(run='echo hi')
    task.set_resources(sky.Resources(infra='k8s/ctx1'))
    client = {'kubernetes': {'context_configs': {context: _docker('theirs')}}}
    with skypilot_config.override_skypilot_config(client, from_loopback=False):
        assert admin_policy_utils._user_set_cache_volumes(task) == expected


def test_cache_volume_is_not_checked_for_other_clouds(server_docker_config):
    task = sky.Task(run='echo hi')
    task.set_resources(sky.Resources(infra='aws'))
    with skypilot_config.override_skypilot_config(
        {'kubernetes': _docker('theirs')}, from_loopback=False):
        assert admin_policy_utils._user_set_cache_volumes(task) == set()


# --- exec mounts nothing ----------------------------------------------------


class _StopAfterVolumes(Exception):
    pass


@pytest.mark.parametrize('request_name, marks', [
    (_Requests.CLUSTER_EXEC, False),
    (_Requests.CLUSTER_LAUNCH, True),
])
def test_exec_does_not_mark_volumes_attached(monkeypatch, request_name, marks):
    dag = mock.MagicMock()
    dag.tasks = []

    @contextlib.contextmanager
    def no_policy(entrypoint, **kwargs):
        del entrypoint, kwargs
        yield dag

    monkeypatch.setattr(admin_policy_utils,
                        'apply_and_use_config_in_current_request', no_policy)
    monkeypatch.setattr(execution, '_resolve_managed_secrets',
                        mock.MagicMock(side_effect=_StopAfterVolumes))
    with pytest.raises(_StopAfterVolumes):
        execution._execute(sky.Task(run='echo hi'), _request_name=request_name)
    assert dag.pre_mount_volumes.called == marks


# --- delete -----------------------------------------------------------------


@contextlib.contextmanager
def _request_state(auth_user):
    with mock.patch.object(fastapi.Request, 'state') as state:
        state.request_id = 'request-id'
        state.auth_user = auth_user
        yield


def _volumes_client():
    app = fastapi.FastAPI()
    app.include_router(server.router, prefix='/volumes')
    return TestClient(app)


@pytest.mark.parametrize('names, auth_user, status', [
    (['mine'], _ALICE, 200),
    (['theirs'], _ALICE, 403),
    (['mine', 'theirs'], _ALICE, 403),
    (['theirs'], None, 200),
])
def test_delete_is_gated_by_each_volumes_workspace(monkeypatch, names,
                                                   auth_user, status):
    schedule = mock.AsyncMock()
    monkeypatch.setattr(executor, 'schedule_request_async', schedule)

    def check(user, name):
        if name == 'theirs':
            raise exceptions.PermissionDeniedError('no access')

    monkeypatch.setattr(workspaces_core, 'check_volume_write_permission', check)
    with _request_state(auth_user):
        response = _volumes_client().post('/volumes/delete',
                                          json={'names': names})
    assert response.status_code == status
    # A refused request deletes nothing, not even the volumes it may delete.
    assert schedule.called == (status == 200)


# --- validate ---------------------------------------------------------------


@pytest.mark.parametrize('infra, status', [('k8s/ctx1', 200),
                                           ('k8s/ctx2', 400)])
def test_validate_checks_infra_in_the_requesters_workspace(
        monkeypatch, infra, status):
    events = []

    @contextlib.contextmanager
    def requester_env(*args, **kwargs):
        events.append('enter')
        yield
        events.append('exit')

    def infra_check(cloud, region):
        events.append(('check', cloud, region))
        if region != 'ctx1':
            raise ValueError(f'Kubernetes context {region!r} is not allowed')

    monkeypatch.setattr(executor, 'stamp_request_body', mock.MagicMock())
    monkeypatch.setattr(executor, 'override_request_env_and_config',
                        requester_env)
    monkeypatch.setattr(core, 'check_volume_infra_allowed', infra_check)
    with _request_state(_ALICE):
        response = _volumes_client().post('/volumes/validate',
                                          json={
                                              'name': 'vol',
                                              'volume_type': 'k8s-pvc',
                                              'infra': infra,
                                              'size': '1Gi',
                                          })
    assert response.status_code == status
    region = infra.split('/')[1]
    # Checked inside the requester's environment, where the workspace is.
    assert events[:2] == ['enter', ('check', 'kubernetes', region)]


def test_validate_does_not_hold_an_existing_volume_to_current_infra(
        monkeypatch):
    """Re-applying is a no-op in /volumes/apply even if the context was
    dropped since; the CLI validates first, so validate must agree."""
    monkeypatch.setattr(executor, 'stamp_request_body', mock.MagicMock())
    monkeypatch.setattr(executor, 'override_request_env_and_config',
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                        lambda name: _volume_record('ws-a'))
    infra_check = mock.MagicMock(side_effect=ValueError('not allowed'))
    monkeypatch.setattr(core, 'check_volume_infra_allowed', infra_check)
    with _request_state(_ALICE):
        response = _volumes_client().post('/volumes/validate',
                                          json={
                                              'name': 'vol',
                                              'volume_type': 'k8s-pvc',
                                              'infra': 'k8s/ctx2',
                                              'size': '1Gi',
                                          })
    assert response.status_code == 200
    infra_check.assert_not_called()


def test_validate_refuses_a_requester_without_a_workspace(monkeypatch):

    @contextlib.contextmanager
    def no_workspace(*args, **kwargs):
        raise exceptions.PermissionDeniedError('no accessible workspace')
        yield  # pylint: disable=unreachable

    monkeypatch.setattr(executor, 'stamp_request_body', mock.MagicMock())
    monkeypatch.setattr(executor, 'override_request_env_and_config',
                        no_workspace)
    with _request_state(_ALICE):
        response = _volumes_client().post('/volumes/validate',
                                          json={
                                              'name': 'vol',
                                              'volume_type': 'k8s-pvc',
                                              'infra': 'k8s/ctx1',
                                              'size': '1Gi',
                                          })
    assert response.status_code == 400
    assert 'no accessible workspace' in response.json()['detail']['message']


# --- create -----------------------------------------------------------------


@pytest.fixture
def k8s_infra(monkeypatch):
    """Kubernetes enabled, with ctx1 the only allowed context."""
    enabled = [clouds.Kubernetes()]
    monkeypatch.setattr(core.sky_check, 'get_cached_enabled_clouds_or_refresh',
                        lambda capability: enabled)
    monkeypatch.setattr(core.sky_check, 'recheck_clouds', mock.MagicMock())
    monkeypatch.setattr(clouds.Kubernetes, 'existing_allowed_contexts',
                        classmethod(lambda cls, silent=False: ['ctx1']))
    return enabled


def test_infra_check_allows_an_allowed_context(k8s_infra):
    core.check_volume_infra_allowed('kubernetes', 'ctx1')
    core.check_volume_infra_allowed('kubernetes', None)


def test_infra_check_refuses_a_context_the_workspace_does_not_allow(k8s_infra):
    with pytest.raises(ValueError, match="'ctx2' is not allowed"):
        core.check_volume_infra_allowed('kubernetes', 'ctx2')


def test_infra_check_refuses_a_cloud_the_workspace_has_not_enabled(k8s_infra):
    with pytest.raises(ValueError, match='not enabled'):
        core.check_volume_infra_allowed('runpod', None)
    # A stale cache is re-checked before refusing.
    core.sky_check.recheck_clouds.assert_called_once()


def test_infra_check_does_not_apply_kubernetes_contexts_to_ssh(k8s_infra):
    """SSH is a Kubernetes subclass; its pools are checked elsewhere."""
    k8s_infra.append(clouds.SSH())
    core.check_volume_infra_allowed('ssh', 'ssh-my-pool')


@pytest.fixture
def apply_deps(monkeypatch):
    """Stubs the cloud side of volume_apply; records what it did, in order."""
    events = []
    record = {'current': None}

    def lookup(name):
        events.append('lookup')
        return record['current']

    @contextlib.contextmanager
    def lock(*args, **kwargs):
        events.append('lock')
        yield

    monkeypatch.setattr(global_user_state, 'get_volume_by_name', lookup)
    monkeypatch.setattr(core.filelock, 'FileLock', lock)
    monkeypatch.setattr(
        core, 'check_volume_infra_allowed',
        mock.MagicMock(side_effect=lambda *a: events.append('infra')))
    monkeypatch.setattr(provision, 'apply_volume',
                        mock.MagicMock(side_effect=lambda cloud, c: c))
    monkeypatch.setattr(provision, 'get_all_volumes_state',
                        lambda cloud, configs: ({}, {}, set()))
    monkeypatch.setattr(clouds.Kubernetes, 'validate_region_zone',
                        lambda self, region, zone: (region, zone))
    add = mock.MagicMock()
    monkeypatch.setattr(global_user_state, 'add_volume', add)
    monkeypatch.setattr(core.skypilot_config, 'get_active_workspace',
                        lambda: 'ws-a')
    monkeypatch.setattr(core.common_utils, 'get_current_user', lambda: _ALICE)
    return events, record, add


def _apply(is_ephemeral=False):
    core.volume_apply(name='vol',
                      volume_type='k8s-pvc',
                      cloud='kubernetes',
                      region='ctx1',
                      zone=None,
                      size='1',
                      config={},
                      is_ephemeral=is_ephemeral)


def test_infra_is_checked_outside_the_lock(apply_deps):
    events, _, add = apply_deps
    _apply()
    assert events == ['lookup', 'infra', 'lock', 'lookup']
    add.assert_called_once()


def test_reapplying_from_the_volumes_workspace_is_a_noop(apply_deps):
    events, record, add = apply_deps
    record['current'] = _volume_record('ws-a')
    _apply()
    # Before the infra check: a context removed since still re-applies.
    assert events == ['lookup']
    add.assert_not_called()


@pytest.mark.parametrize('readable, where', [(True, "workspace 'ws-b'"),
                                             (False, 'another workspace')])
def test_reapplying_a_name_held_by_another_workspace_fails(
        monkeypatch, apply_deps, readable, where):
    _, record, add = apply_deps
    record['current'] = _volume_record('ws-b')
    monkeypatch.setattr(workspaces_core, 'get_readable_workspace_names',
                        lambda: {'ws-a', 'ws-b'} if readable else {'ws-a'})
    with pytest.raises(ValueError, match=f'already exists in {where}'):
        _apply()
    add.assert_not_called()


def test_an_ephemeral_volume_reapplied_from_another_workspace_is_a_noop(
        apply_deps):
    """`sky start` re-applies a cluster's ephemeral volumes from the caller's
    active workspace, which may not be the cluster's."""
    events, record, _ = apply_deps
    record['current'] = _volume_record('ws-b')
    _apply(is_ephemeral=True)
    assert 'infra' not in events


def test_the_recheck_under_the_lock_compares_workspaces(monkeypatch,
                                                        apply_deps):
    """Another workspace created the name between the lookup and the lock."""
    _, _, add = apply_deps
    lookups = iter([None, _volume_record('ws-b')])
    monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                        lambda name: next(lookups))
    monkeypatch.setattr(workspaces_core, 'get_readable_workspace_names',
                        lambda: {'ws-a'})
    with pytest.raises(ValueError, match='another workspace'):
        _apply()
    add.assert_not_called()


# --- duplicate backend resource ---------------------------------------------


@pytest.mark.parametrize('readable', [True, False])
def test_duplicate_backend_error_names_only_a_readable_volume(
        monkeypatch, readable):
    other = _volume_record('ws-b')
    other['name'] = 'other-vol'
    monkeypatch.setattr(global_user_state, 'get_volumes', lambda: [other])
    monkeypatch.setattr(workspaces_core, 'get_readable_workspace_names',
                        lambda: {'ws-a', 'ws-b'} if readable else {'ws-a'})
    with pytest.raises(ValueError) as e:
        core._check_duplicate_backend_resource('vol', other['handle'])
    assert ('other-vol' in str(e.value)) == readable


# --- name completion --------------------------------------------------------


def test_volume_name_completion_is_limited_to_readable_workspaces(monkeypatch):
    monkeypatch.setattr(api_server.common,
                        'refresh_workspace_state_for_sync_handler',
                        mock.MagicMock())
    monkeypatch.setattr(permission.permission_service,
                        'get_accessible_workspace_names',
                        lambda user_id, names, action: {'ws-a'})
    lookup = mock.MagicMock(return_value=['vol-a'])
    monkeypatch.setattr(global_user_state, 'get_volume_names_start_with',
                        lookup)

    def complete(auth_user):
        request = mock.MagicMock()
        request.state.auth_user = auth_user
        return asyncio.run(api_server.complete_volume_name(request, 'vol'))

    assert complete(_ALICE) == ['vol-a']
    lookup.assert_called_with('vol', workspaces_filter={'ws-a'})
    complete(None)
    lookup.assert_called_with('vol', workspaces_filter=None)
