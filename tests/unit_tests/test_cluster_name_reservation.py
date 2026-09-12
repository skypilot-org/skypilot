import concurrent.futures
import contextlib
import pathlib
import threading
from types import SimpleNamespace
from typing import Any, Callable, ContextManager, Dict, Iterator, List, Optional
from unittest import mock

import pytest
import sqlalchemy

from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.backends import cluster_name
from sky.resources import Resources
from sky.utils import common_utils
from sky.utils import locks
from sky.utils import registry
from sky.utils import status_lib
from sky.utils import yaml_utils


@pytest.fixture
def state(tmp_path: pathlib.Path,
          monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/state.db')
    monkeypatch.setattr(global_user_state._db_manager, 'get_engine',
                        lambda: engine)
    global_user_state.create_table(engine)
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    monkeypatch.setattr(locks, '_detect_lock_type', lambda: 'filelock')
    monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: False)
    monkeypatch.setattr(common_utils, 'get_current_user',
                        lambda: models.User('test-user', 'test'))
    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: 'test-user')
    monkeypatch.setattr(skypilot_config, 'get_active_workspace',
                        lambda: 'default')
    yield
    engine.dispose()


def config(name: str = 'my-cluster-test-user',
           **provider: Any) -> Dict[str, Any]:
    return {
        'cluster_name': name,
        'auth': {
            'ssh_user': 'test-user'
        },
        'provider': {
            'module': 'sky.provision.azure',
            'subscription_id': 'account',
            'resource_group': 'workers',
            **provider,
        },
    }


def cloud_for(rendered: Dict[str, Any]) -> clouds.Cloud:
    provider = rendered['provider']
    name = provider.get('module', provider.get('type')).split('.')[-1]
    return registry.CLOUD_REGISTRY.from_str(name)


def reserve(name: str, rendered: Dict[str, Any],
            owner: Optional[List[str]]) -> ContextManager[None]:
    return cluster_name.reserve(name, rendered, cloud_for(rendered), owner)


def save_cluster(name: str,
                 rendered: Dict[str, Any],
                 ready: bool = False,
                 owner: Optional[List[str]] = None) -> None:
    handle = SimpleNamespace(
        cluster_name=name,
        cluster_name_on_cloud=rendered['cluster_name'],
        cluster_yaml=f'/unused/{name}.yml',
        launched_resources=SimpleNamespace(cloud=cloud_for(rendered),
                                           region='eastus',
                                           zone=None),
    )
    global_user_state.set_cluster_yaml(name, yaml_utils.dump_yaml_str(rendered))
    global_user_state.add_or_update_cluster(name, handle, None, ready=ready)
    global_user_state.set_owner_identity_for_cluster(name, owner)


@pytest.mark.parametrize('status', [
    status_lib.ClusterStatus.INIT, status_lib.ClusterStatus.UP,
    status_lib.ClusterStatus.STOPPED
])
def test_live_status_reserves_cloud_name(
        state: None, status: status_lib.ClusterStatus) -> None:
    rendered = config()
    save_cluster('my-cluster',
                 rendered,
                 ready=status == status_lib.ClusterStatus.UP)
    if status == status_lib.ClusterStatus.STOPPED:
        global_user_state.remove_cluster('my-cluster', terminate=False)
    with pytest.raises(exceptions.ClusterNameCollisionError,
                       match='my-cluster'):
        with reserve('my_cluster', rendered, None):
            pytest.fail('Collision reached provisioning')
    assert global_user_state.get_cluster_from_name('my_cluster') is None
    assert global_user_state.get_cluster_from_name(
        'my-cluster')['status'] == status


def test_same_logical_cluster_preserves_persisted_name(state: None) -> None:
    rendered = config('persisted-name-from-an-older-release')
    save_cluster('My.Cluster', rendered)
    with reserve('My.Cluster', rendered, None):
        save_cluster('My.Cluster', rendered, ready=True)
    handle = global_user_state.get_handle_from_cluster_name('My.Cluster')
    assert handle.cluster_name_on_cloud == rendered['cluster_name']


def test_terminated_history_does_not_reserve_name(state: None) -> None:
    rendered = config()
    save_cluster('my-cluster', rendered, ready=True)
    global_user_state.remove_cluster('my-cluster', terminate=True)
    assert global_user_state.get_cluster_yaml_str('/unused/my-cluster.yml')
    with reserve('my_cluster', rendered, None):
        save_cluster('my_cluster', rendered)


@pytest.mark.parametrize('provider,other,owner,other_owner', [
    ({}, {
        'resource_group': 'other-workers'
    }, None, None),
    ({}, {
        'subscription_id': 'other-account'
    }, None, None),
    ({
        'module': 'sky.provision.gcp',
        'project_id': 'a'
    }, {
        'module': 'sky.provision.gcp',
        'project_id': 'b'
    }, None, None),
    ({
        'module': 'sky.provision.kubernetes',
        'context': 'ctx',
        'namespace': 'a'
    }, {
        'module': 'sky.provision.kubernetes',
        'context': 'ctx',
        'namespace': 'b'
    }, None, None),
    ({
        'module': 'sky.provision.aws',
        'region': 'us-east-1'
    }, {
        'module': 'sky.provision.aws',
        'region': 'us-east-1'
    }, ['principal-a', 'a'], ['principal-b', 'b']),
    ({
        'module': 'sky.provision.aws',
        'region': 'us-east-1'
    }, {
        'module': 'sky.provision.aws',
        'region': 'us-west-2'
    }, ['principal', 'a'], ['principal', 'a']),
])
def test_disjoint_provider_namespaces(state: None, provider: Dict[str, Any],
                                      other: Dict[str, Any],
                                      owner: Optional[List[str]],
                                      other_owner: Optional[List[str]]) -> None:
    save_cluster('first', config(**provider), owner=owner)
    with reserve('second', config(**other), other_owner):
        save_cluster('second', config(**other), owner=other_owner)


def test_same_aws_account_different_principal_collides(state: None) -> None:
    rendered = config(module='sky.provision.aws', region='us-east-1')
    save_cluster('first', rendered, owner=['role-a', 'account'])
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve('second', rendered, ['role-b', 'account']):
            pytest.fail('Principal changes must not bypass account ownership')


def test_kubernetes_context_aliases_do_not_prove_disjoint_namespaces(
        state: None) -> None:
    save_cluster(
        'first',
        config(module='sky.provision.kubernetes', context='a',
               namespace='same'))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve(
                'second',
                config(module='sky.provision.kubernetes',
                       context='b',
                       namespace='same'), None):
            pytest.fail('Kubeconfig contexts may refer to the same cluster')


def test_missing_legacy_namespace_fails_closed(state: None) -> None:
    save_cluster('first', config(subscription_id=None, resource_group=None))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve('second', config(), None):
            pytest.fail('Missing metadata cannot prove a different namespace')


def test_omitted_kubernetes_namespace_is_not_assumed_default(
        state: None) -> None:
    save_cluster('first', config(module='sky.provision.kubernetes',
                                 context='a'))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve(
                'second',
                config(module='sky.provision.kubernetes',
                       context='b',
                       namespace='workers'), None):
            pytest.fail('An omitted namespace may resolve to workers')


def test_legacy_provider_spelling_and_azure_case_collide(state: None) -> None:
    save_cluster(
        'first',
        config(module='azure',
               resource_group='WORKERS',
               subscription_id='ACCOUNT'))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve('second', config(), None):
            pytest.fail('Provider aliases must use the same namespace')


def test_concurrent_reservation_commits_before_contender_checks(
        state: None) -> None:
    entered = threading.Event()
    release = threading.Event()
    contender_started = threading.Event()
    rendered = config()

    def first() -> None:
        with reserve('my-cluster', rendered, None):
            entered.set()
            assert release.wait(5)
            save_cluster('my-cluster', rendered)

    def second() -> None:
        assert entered.wait(5)
        contender_started.set()
        with pytest.raises(exceptions.ClusterNameCollisionError):
            with reserve('my_cluster', rendered, None):
                pytest.fail('Concurrent launch adopted the first reservation')

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(first)
        contender = executor.submit(second)
        assert contender_started.wait(5)
        release.set()
        winner.result(timeout=5)
        contender.result(timeout=5)
    assert global_user_state.get_cluster_from_name('my_cluster') is None


def test_reservation_exception_releases_lock_without_persisting(
        state: None) -> None:
    rendered = config()
    with pytest.raises(RuntimeError, match='before INIT'):
        with reserve('first', rendered, None):
            raise RuntimeError('before INIT')
    with reserve('second', rendered, None):
        save_cluster('second', rendered)


def test_exit_stack_holds_reservation_until_init_commit(
        state: None, monkeypatch: pytest.MonkeyPatch) -> None:
    rendered = config()
    with contextlib.ExitStack() as stack:
        stack.enter_context(reserve('first', rendered, None))
        monkeypatch.setattr(common_utils, 'is_in_request_context', lambda: True)
        with pytest.raises(exceptions.ExecutionPausedError) as paused:
            with reserve('second', rendered, None):
                pytest.fail('Contender acquired an unfinished reservation')
        assert isinstance(paused.value.continue_condition,
                          locks.LockAcquirableCondition)
        save_cluster('first', rendered)
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve('second', rendered, None):
            pytest.fail('Committed INIT name is still reserved')


@pytest.mark.parametrize('first,second,user', [
    ('My-Cluster', 'my_cluster', 'test-user'),
    ('my.cluster', 'my-cluster', 'test-user'),
    ('managed-job-collision-0007-7', 'managed-job-collision-0017-17',
     'sa-0000000000000000'),
])
def test_native_normalization_and_truncation_hash_collision(
        state: None, monkeypatch: pytest.MonkeyPatch, first: str, second: str,
        user: str) -> None:
    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: user)
    first_cloud = common_utils.make_cluster_name_on_cloud(first, max_length=42)
    second_cloud = common_utils.make_cluster_name_on_cloud(second,
                                                           max_length=42)
    assert first_cloud == second_cloud
    save_cluster(first, config(first_cloud))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        with reserve(second, config(second_cloud), None):
            pytest.fail('Native alias must be rejected')


@pytest.fixture
def write_config(
        state: None, tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch) -> Callable[..., Dict[str, Any]]:
    monkeypatch.setattr(Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr(backend_utils.sky_check,
                        'get_cloud_credential_file_mounts', lambda *args: {})
    monkeypatch.setattr(backend_utils.auth_utils, 'get_or_generate_keys',
                        lambda: ('/unused/private-key', '/unused/public-key'))
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda name: str(tmp_path / f'{name}.yml'))
    monkeypatch.setattr(backend_utils, '_optimize_file_mounts',
                        lambda path: None)
    monkeypatch.setattr(backend_utils, '_deterministic_cluster_yaml_hash',
                        lambda path: 'hash')
    monkeypatch.setattr(backend_utils.usage_lib.messages.usage,
                        'update_ray_yaml', lambda path: None)

    def fill_template(template: str, variables: Dict[str, Any],
                      output_path: str) -> None:
        rendered = config(variables['cluster_name_on_cloud'])
        rendered['auth'] = {'ssh_user': 'rendered-user'}
        yaml_utils.dump_yaml(output_path, rendered)

    monkeypatch.setattr(common_utils, 'fill_template', fill_template)

    def write(name: str, stack: contextlib.ExitStack,
              **kwargs: Any) -> Dict[str, Any]:
        return backend_utils.write_cluster_config(Resources(
            cloud=clouds.Azure(), instance_type='test'),
                                                  1,
                                                  'unused-template',
                                                  name,
                                                  pathlib.Path('/unused/wheel'),
                                                  'wheel-hash',
                                                  clouds.Region('eastus'),
                                                  name_reservation=stack,
                                                  **kwargs)

    return write


def test_config_collision_precedes_auth_and_yaml_commit(
        write_config: Callable[..., Dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    native_name = common_utils.make_cluster_name_on_cloud('my-cluster', 42)
    save_cluster('my-cluster', config(native_name))
    auth = mock.Mock()
    monkeypatch.setattr(backend_utils, '_add_auth_to_cluster_config', auth)
    with contextlib.ExitStack() as stack:
        with pytest.raises(exceptions.ClusterNameCollisionError):
            write_config('my_cluster', stack)
    auth.assert_not_called()
    assert global_user_state.get_cluster_yaml_str(
        '/unused/my_cluster.yml') is None
    assert global_user_state.get_cluster_from_name('my_cluster') is None


def test_existing_config_preserves_auth_after_auth_setup(
        write_config: Callable[..., Dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    persisted = config('persisted-name')
    persisted['auth'] = {
        'ssh_user': 'existing-user',
        'ssh_private_key': '/existing/private-key',
        'ssh_proxy_command': 'existing-proxy %h %p',
    }
    save_cluster('existing', persisted)

    def change_auth(cloud: clouds.Cloud, path: str) -> None:
        rendered = yaml_utils.read_yaml(path)
        rendered['auth'] = {
            'ssh_user': 'new-user',
            'ssh_private_key': '/new/private-key',
            'ssh_proxy_command': 'new-proxy %h %p'
        }
        yaml_utils.dump_yaml(path, rendered)

    monkeypatch.setattr(backend_utils, '_add_auth_to_cluster_config',
                        change_auth)
    with contextlib.ExitStack() as stack:
        result = write_config('existing', stack)
        restored = yaml_utils.safe_load(
            global_user_state.get_cluster_yaml_str(result['ray']))
        assert restored['cluster_name'] == persisted['cluster_name']
        assert restored['auth'] == persisted['auth']


@pytest.fixture
def provision(write_config: Callable[..., Dict[str,
                                               Any]], tmp_path: pathlib.Path,
              monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(Resources, 'assert_launchable', lambda self: self)
    monkeypatch.setattr(clouds.Azure, 'check_quota_available',
                        lambda *args: True)
    monkeypatch.setattr(clouds.Azure, 'make_deploy_resources_variables',
                        lambda *args: {})
    monkeypatch.setattr(cloud_vm_ray_backend.provision_lib,
                        'get_registered_provisioner', lambda cloud: None)
    monkeypatch.setattr(cloud_vm_ray_backend.RetryingVmProvisioner,
                        '_yield_zones', lambda *args: iter([None]))
    auth = mock.Mock()
    bulk = mock.Mock()
    cleanup = mock.Mock()
    monkeypatch.setattr(backend_utils, '_add_auth_to_cluster_config', auth)
    monkeypatch.setattr(cloud_vm_ray_backend.provisioner, 'bulk_provision',
                        bulk)
    monkeypatch.setattr(cloud_vm_ray_backend.CloudVmRayBackend,
                        'post_teardown_cleanup', cleanup)
    provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
        str(tmp_path / 'logs'),
        None,
        None,
        set(),
        pathlib.Path('/unused/wheel'),
        'wheel-hash',
        is_managed=False,
        extra_launch_context={})

    def launch(name: str, existing: bool = False) -> Dict[str, Any]:
        resources = Resources(cloud=clouds.Azure(),
                              instance_type='test',
                              region='eastus')
        previous = (global_user_state.get_handle_from_cluster_name(name)
                    if existing else None)
        return provisioner._retry_zones(
            resources, 1, {resources}, False, False, name, None,
            status_lib.ClusterStatus.INIT if existing else None, previous,
            False, None, None, None)

    return SimpleNamespace(launch=launch, auth=auth, bulk=bulk, cleanup=cleanup)


@pytest.mark.parametrize('first,second,user', [
    ('My-Cluster', 'my_cluster', 'test-user'),
    ('managed-job-collision-0007-7', 'managed-job-collision-0017-17',
     'sa-0000000000000000'),
])
def test_new_colliding_launches_allocate_and_persist_distinct_names(
        provision: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, first: str,
        second: str, user: str) -> None:
    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: user)
    one = provision.launch(first)
    two = provision.launch(second)
    assert one['cluster_name_on_cloud'] != two['cluster_name_on_cloud']
    assert one[
        'cluster_name_on_cloud'] == common_utils.make_cluster_name_on_cloud(
            first, 42)
    assert len(two['cluster_name_on_cloud']) <= 42
    assert two['cluster_name_on_cloud'].endswith('-' + user)
    # The rejected candidate must not reach authentication or provisioning.
    assert provision.auth.call_count == provision.bulk.call_count == 2
    provision.cleanup.assert_not_called()
    assert cluster_name.candidates(
        second, clouds.Azure()) == [two['cluster_name_on_cloud']]

    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: 'another-user')
    retry = provision.launch(second, existing=True)
    assert retry['cluster_name_on_cloud'] == two['cluster_name_on_cloud']
    persisted = yaml_utils.safe_load(
        global_user_state.get_cluster_yaml_str(retry['ray']))
    assert persisted['cluster_name'] == two['cluster_name_on_cloud']


def test_allocation_exhaustion_precedes_every_cloud_mutator(
        provision: SimpleNamespace) -> None:
    for i, name in enumerate(
            cluster_name.candidates('new-cluster', clouds.Azure())):
        save_cluster(f'owner-{i}', config(name))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        provision.launch('new-cluster')
    provision.auth.assert_not_called()
    provision.bulk.assert_not_called()
    provision.cleanup.assert_not_called()
    assert global_user_state.get_cluster_from_name('new-cluster') is None
    assert global_user_state.get_cluster_yaml_str(
        '/unused/new-cluster.yml') is None


def test_concurrent_launches_allocate_distinct_committed_handles(
        provision: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    first_render = threading.Barrier(2)
    fill_template = common_utils.fill_template
    default_name = common_utils.make_cluster_name_on_cloud('my-cluster', 42)

    def synchronized_template(template: str, variables: Dict[str, Any],
                              output_path: str) -> None:
        fill_template(template, variables, output_path)
        if variables['cluster_name_on_cloud'] == default_name:
            first_render.wait(timeout=5)

    monkeypatch.setattr(common_utils, 'fill_template', synchronized_template)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(provision.launch, 'my-cluster')
        second = executor.submit(provision.launch, 'my_cluster')
        results = [first.result(timeout=10), second.result(timeout=10)]
    names = {result['cluster_name_on_cloud'] for result in results}
    assert len(names) == 2
    assert default_name in names
    assert {
        global_user_state.get_handle_from_cluster_name(
            name).cluster_name_on_cloud for name in ('my-cluster', 'my_cluster')
    } == names
    assert {
        call.args[3].name_on_cloud for call in provision.bulk.call_args_list
    } == names
    assert provision.auth.call_count == provision.bulk.call_count == 2
    provision.cleanup.assert_not_called()


def test_constant_custom_template_exhausts_before_cloud_mutation(
        provision: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    save_cluster('victim', config('constant-cloud-name'))
    fill_template = common_utils.fill_template
    renders = 0

    def constant_template(template: str, variables: Dict[str, Any],
                          output_path: str) -> None:
        nonlocal renders
        renders += 1
        fill_template(template, variables, output_path)
        rendered = yaml_utils.read_yaml(output_path)
        rendered['cluster_name'] = 'constant-cloud-name'
        yaml_utils.dump_yaml(output_path, rendered)

    monkeypatch.setattr(common_utils, 'fill_template', constant_template)
    with pytest.raises(exceptions.ClusterNameCollisionError):
        provision.launch('new-cluster')
    assert renders == 9
    provision.auth.assert_not_called()
    provision.bulk.assert_not_called()
    provision.cleanup.assert_not_called()
    assert global_user_state.get_cluster_from_name('new-cluster') is None


def test_existing_collision_cannot_allocate_away_from_persisted_resources(
        provision: SimpleNamespace) -> None:
    name = common_utils.make_cluster_name_on_cloud('first', 42)
    save_cluster('first', config(name))
    save_cluster('second', config(name))
    with pytest.raises(exceptions.ClusterNameCollisionError):
        provision.launch('second', existing=True)
    provision.auth.assert_not_called()
    provision.bulk.assert_not_called()
    provision.cleanup.assert_not_called()


@pytest.mark.parametrize('maximum', [15, 35, 42, None])
def test_alternative_names_respect_provider_limits(
        state: None, monkeypatch: pytest.MonkeyPatch,
        maximum: Optional[int]) -> None:
    monkeypatch.setattr(clouds.Azure, 'max_cluster_name_length',
                        lambda self: maximum)
    monkeypatch.setattr(common_utils, 'get_user_hash', lambda: '12345678')
    names = cluster_name.candidates('my-cluster-with-a-long-name',
                                    clouds.Azure())
    assert len(set(names)) == 9
    assert all(name.endswith('-12345678') for name in names)
    assert maximum is None or all(len(name) <= maximum for name in names)
