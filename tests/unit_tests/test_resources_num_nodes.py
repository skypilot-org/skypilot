"""Unit tests for `num_nodes` set on resources (e.g., per any_of candidate)."""
import asyncio
import pickle
from unittest import mock

import pytest

from sky import clouds
from sky import core
from sky import dag as dag_lib
from sky import exceptions
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.client import sdk
from sky.client.cli import command
from sky.jobs import recovery_strategy
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import constants as server_constants
from sky.utils import dag_utils
from sky.utils import resources_utils
from sky.utils import status_lib


def _task_from_config(config):
    return task_lib.Task.from_yaml_config(config)


def test_parse_per_candidate_num_nodes():
    task = _task_from_config({
        'resources': {
            'ordered': [
                {
                    'accelerators': 'H200:8',
                    'num_nodes': 1
                },
                {
                    'accelerators': 'H100:8',
                    'num_nodes': 2
                },
                {
                    'accelerators': 'A100:8'
                },
            ]
        },
    })
    assert [r.num_nodes for r in task.resources] == [1, 2, 1]
    # Candidates without their own count use the default of 1 node.
    assert [(r).num_nodes for r in task.resources] == [1, 2, 1]
    assert [r.num_nodes for r in task.resources] == [1, 2, 1]
    # A single count is ambiguous until a candidate is chosen.
    with pytest.raises(ValueError, match="different node counts"):
        _ = task.num_nodes


def test_top_level_resources_num_nodes_is_candidate_default():
    task = _task_from_config({
        'resources': {
            'num_nodes': 2,
            'any_of': [
                {
                    'accelerators': 'H100:8'
                },
                {
                    'accelerators': 'H200:8',
                    'num_nodes': 1
                },
            ],
        },
    })
    counts = {next(iter(r.accelerators)): r.num_nodes for r in task.resources}
    assert counts == {'H100': 2, 'H200': 1}


def test_single_resources_num_nodes():
    task = _task_from_config({
        'resources': {
            'accelerators': 'H100:8',
            'num_nodes': 4
        },
    })
    assert task.num_nodes == 4


@pytest.mark.parametrize(
    'resources_config',
    [
        # Once for all candidates.
        {
            'num_nodes': 2,
            'any_of': [{
                'accelerators': 'H100:8'
            }, {
                'accelerators': 'A100:8'
            }]
        },
        # Per candidate, even if only on some.
        {
            'any_of': [{
                'accelerators': 'H100:8',
                'num_nodes': 2
            }, {
                'accelerators': 'A100:8'
            }]
        },
        {
            'accelerators': 'H100:8',
            'num_nodes': 2
        },
    ])
@pytest.mark.parametrize('task_num_nodes', [1, 3])
def test_num_nodes_on_task_and_resources_rejected(task_num_nodes,
                                                  resources_config):
    with pytest.raises(ValueError, match='Set it in one place only'):
        _task_from_config({
            'num_nodes': task_num_nodes,
            'resources': resources_config
        })


def test_num_nodes_on_task_and_resources_rejected_in_sdk():
    counted = resources_lib.Resources(accelerators='H100:8', num_nodes=2)
    # Task count first, then resources.
    task = task_lib.Task(num_nodes=4)
    with pytest.raises(ValueError, match='Set it in one place only'):
        task.set_resources(counted)
    with pytest.raises(ValueError, match='Set it in one place only'):
        task_lib.Task(num_nodes=4, resources=counted)
    with pytest.raises(ValueError, match='Set it in one place only'):
        task_lib.Task(num_nodes=4).set_resources_override({'num_nodes': 2})
    # Resources first, then the task count.
    task = task_lib.Task().set_resources(counted)
    with pytest.raises(ValueError, match='Set it in one place only'):
        task.num_nodes = 4
    # Unsetting the task count is always allowed.
    task.num_nodes = None
    assert task.num_nodes == 2


@pytest.mark.parametrize('resources_config, expected', [
    ({
        'any_of': [{
            'accelerators': 'H100:8',
            'num_nodes': 2
        }, {
            'accelerators': 'A100:8',
            'num_nodes': 1
        }]
    }, [4, 4]),
    ({
        'accelerators': 'H100:8'
    }, [4]),
])
def test_cli_num_nodes_overrides_where_set(tmp_path, resources_config,
                                           expected):
    yaml_path = tmp_path / 'task.yaml'
    yaml_path.write_text(
        dag_utils.yaml_utils.dump_yaml_str({
            'resources': resources_config,
            'run': 'echo hi'
        }))
    task = command._make_task_or_dag_from_entrypoint_with_overrides(  # pylint: disable=protected-access
        entrypoint=(str(yaml_path),),
        num_nodes=4)
    assert [r.num_nodes for r in task.resources] == expected
    # The task-level count is set only if the resources don't set one.
    assert ('num_nodes' in task.to_yaml_config()) == (expected == [4])


@pytest.mark.parametrize('bad_value', [0, -1, 'two', 1.5, True])
def test_invalid_num_nodes_rejected(bad_value):
    with pytest.raises((ValueError, exceptions.InvalidSkyPilotConfigError)):
        _task_from_config({'resources': {'num_nodes': bad_value}})
    with pytest.raises(ValueError):
        resources_lib.Resources(num_nodes=bad_value)


def test_num_nodes_round_trip():
    task = _task_from_config({
        'resources': {
            'any_of': [
                {
                    'accelerators': 'H200:8',
                    'num_nodes': 1
                },
                {
                    'accelerators': 'A100:8',
                    'num_nodes': 3
                },
            ]
        },
    })
    # The chosen resources' count must not leak into a task-level count
    # when serialized (e.g., to the API server or the jobs controller), or
    # the restored task would set it in both places.
    task.best_resources = next(r for r in task.resources if r.num_nodes == 1)
    assert task.num_nodes == 1
    config = task.to_yaml_config()
    assert 'num_nodes' not in config
    restored = _task_from_config(config)
    assert sorted((r).num_nodes for r in restored.resources) == [1, 3]

    # Through the DAG YAML used by the client and the jobs controller.
    dag = dag_utils.convert_entrypoint_to_dag(task)
    restored_dag = dag_utils.load_chain_dag_from_yaml_str(
        dag_utils.dump_chain_dag_to_yaml_str(dag))
    restored = restored_dag.tasks[0]
    assert sorted((r).num_nodes for r in restored.resources) == [1, 3]


def test_task_level_num_nodes_round_trip():
    config = task_lib.Task(num_nodes=3).to_yaml_config()
    assert config['num_nodes'] == 3
    assert _task_from_config(config).num_nodes == 3
    # The default is not written out.
    assert 'num_nodes' not in task_lib.Task().to_yaml_config()


def test_resources_copy_and_repr():
    r = resources_lib.Resources(accelerators='H100:8', num_nodes=2)
    assert r.copy().num_nodes == 2
    assert r.copy(num_nodes=None).num_nodes == 1
    assert not r.copy(num_nodes=None).num_nodes_specified
    assert 'num_nodes=2' in repr(r)
    assert 'num_nodes' not in repr(
        resources_lib.Resources(accelerators='H100:8'))
    assert not r.is_empty()


def test_resources_unpickle_old_version():
    r = resources_lib.Resources(accelerators='H100:8')
    state = r.__getstate__()
    state['_version'] = 36
    del state['_num_nodes']
    restored = resources_lib.Resources.__new__(resources_lib.Resources)
    restored.__setstate__(state)
    assert restored.num_nodes == 1
    assert not restored.num_nodes_specified


def test_should_be_blocked_by_num_nodes():
    aws = clouds.AWS()

    def res(num_nodes):
        return resources_lib.Resources(cloud=aws,
                                       instance_type='p5.48xlarge',
                                       num_nodes=num_nodes)

    # Failing to get 4 nodes does not block asking for 2 in the same place.
    assert not res(2).should_be_blocked_by(res(4))
    # Failing to get 2 nodes blocks asking for as many or more.
    assert res(2).should_be_blocked_by(res(2))
    assert res(4).should_be_blocked_by(res(2))
    # No count on either side: matched as before.
    assert res(None).should_be_blocked_by(res(4))
    assert res(4).should_be_blocked_by(res(None))


def test_required_cloud_features_multi_node_only_if_every_candidate():
    multi = clouds.CloudImplementationFeatures.MULTI_NODE
    task = task_lib.Task()
    task.set_resources([
        resources_lib.Resources(accelerators='H200:8', num_nodes=1),
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
    ])
    assert multi not in task.get_required_cloud_features()
    task = task_lib.Task()
    task.set_resources([
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
        resources_lib.Resources(accelerators='A100:8', num_nodes=4),
    ])
    assert multi in task.get_required_cloud_features()


@pytest.mark.parametrize('remote_version, uses_num_nodes, should_raise', [
    (server_constants.MIN_RESOURCES_NUM_NODES_API_VERSION - 1, True, True),
    (None, True, True),
    (server_constants.MIN_RESOURCES_NUM_NODES_API_VERSION, True, False),
    (server_constants.MIN_RESOURCES_NUM_NODES_API_VERSION - 1, False, False),
])
def test_old_server_rejects_resources_num_nodes(remote_version, uses_num_nodes,
                                                should_raise):
    task = task_lib.Task()
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8',
                                num_nodes=1 if uses_num_nodes else None),
        resources_lib.Resources(accelerators='A100:8'),
    })
    dag = dag_utils.convert_entrypoint_to_dag(task)
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=remote_version):
        if should_raise:
            with pytest.raises(exceptions.APINotSupportedError):
                sdk._check_resources_num_nodes_api_version(dag)  # pylint: disable=protected-access
        else:
            sdk._check_resources_num_nodes_api_version(dag)  # pylint: disable=protected-access


def test_check_resources_fit_cluster_prefers_candidate_matching_cluster():
    """On an existing cluster, the candidate using all its nodes runs."""
    task = task_lib.Task()
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8', num_nodes=1),
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
    })
    handle = mock.MagicMock()
    handle.launched_nodes = 2
    handle.cluster_name = 'c'
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    with mock.patch.object(resources_lib.Resources,
                           'less_demanding_than',
                           return_value=True), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name',
                              return_value=None), \
            mock.patch.object(cloud_vm_ray_backend.usage_lib.messages.usage,
                              'update_cluster_resources'):
        valid = backend.check_resources_fit_cluster(handle, task)
        assert valid.num_nodes == 2

        # A candidate needing more nodes than the cluster has never fits.
        handle.launched_nodes = 1
        valid = backend.check_resources_fit_cluster(handle, task)
        assert valid.num_nodes == 1


def test_num_nodes_before_a_candidate_is_chosen():
    # A count shared by all candidates is known before optimization.
    task = _task_from_config({
        'resources': {
            'num_nodes': 2,
            'any_of': [{
                'accelerators': 'H100:8'
            }, {
                'accelerators': 'A100:8'
            }]
        }
    })
    assert task.num_nodes == 2
    # Candidates that differ have no single count until one is chosen.
    task = task_lib.Task()
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8', num_nodes=4),
        resources_lib.Resources(accelerators='A100:8', num_nodes=2),
    })
    with pytest.raises(ValueError, match="different node counts"):
        _ = task.num_nodes
    task.best_resources = next(r for r in task.resources if r.num_nodes == 4)
    assert task.num_nodes == 4


@pytest.mark.parametrize('launched_num_nodes', [None, 3])
def test_start_provisions_launched_node_count(launched_num_nodes):
    """`sky start` restarts the cluster's node count, which now lives on its
    launched resources (or not, for clusters launched before)."""
    launched = resources_lib.Resources(cloud=clouds.AWS(),
                                       instance_type='p5.48xlarge',
                                       num_nodes=launched_num_nodes)
    handle = mock.MagicMock(launched_nodes=3, launched_resources=launched)
    backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)
    backend.provision.side_effect = RuntimeError('stop here')
    with mock.patch.object(core.backend_utils,
                           'refresh_cluster_status_handle',
                           return_value=(status_lib.ClusterStatus.STOPPED,
                                         handle)), \
            mock.patch.object(core.backend_utils,
                              'get_backend_from_handle',
                              return_value=backend), \
            pytest.raises(RuntimeError, match='stop here'):
        core._start('c')  # pylint: disable=protected-access
    task = backend.provision.call_args.args[0]
    to_provision = backend.provision.call_args.kwargs['to_provision']
    assert (to_provision).num_nodes == 3
    assert task.num_nodes == 3


@pytest.mark.parametrize('on_task', [True, False])
def test_failover_same_region_retry_keeps_node_count(on_task):
    """FAILOVER retries the launched placement first, with its node count."""
    task = task_lib.Task(num_nodes=2 if on_task else None)
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8',
                                num_nodes=None if on_task else 2),
        resources_lib.Resources(accelerators='A100:8',
                                num_nodes=None if on_task else 4),
    })
    executor = recovery_strategy.FailoverStrategyExecutor.__new__(
        recovery_strategy.FailoverStrategyExecutor)
    executor.dag = mock.MagicMock(tasks=[task])
    executor._launched_resources = resources_lib.Resources(  # pylint: disable=protected-access
        cloud=clouds.AWS(),
        region='us-east-1',
        instance_type='p5.48xlarge',
        num_nodes=2)
    retried = []

    async def fake_launch(**kwargs):
        del kwargs
        retried.append((list(task.resources), task.num_nodes))
        return 1.0

    executor._try_cancel_jobs = mock.AsyncMock()  # pylint: disable=protected-access
    executor._launch = fake_launch  # pylint: disable=protected-access
    asyncio.run(executor.recover())
    assert len(retried) == 1
    retried_resources, retried_num_nodes = retried[0]
    assert len(retried_resources) == 1
    assert retried_resources[0].region == 'us-east-1'
    assert retried_num_nodes == 2


def test_task_resources_str_shows_node_count_range():
    task = task_lib.Task()
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
        resources_lib.Resources(accelerators='A100:8', num_nodes=4),
    })
    assert backend_utils.get_task_resources_str(task).startswith('2-4x[')
    chosen = next(r for r in task.resources if r.num_nodes == 4)
    task.best_resources = chosen
    assert backend_utils.get_task_resources_str(task).startswith('4x[')
    assert backend_utils.get_task_num_nodes_str(task,
                                                all_candidates=True) == '2-4'
    # Candidates that agree show their single count.
    task = task_lib.Task(num_nodes=3)
    task.set_resources({
        resources_lib.Resources(accelerators='H100:8'),
        resources_lib.Resources(accelerators='A100:8'),
    })
    assert backend_utils.get_task_resources_str(task).startswith('3x[')


def test_resize_uses_count_of_candidate_matching_cluster():
    """--resize grows to the count of the candidate that fits the cluster."""
    task = task_lib.Task()
    task.set_resources([
        resources_lib.Resources(accelerators='H200:8', num_nodes=1),
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
    ])
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='p5.48xlarge')
    handle = mock.MagicMock()
    handle.launched_nodes = 1
    handle.launched_resources = launched
    handle.cluster_name = 'c'
    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_ever_up': True,
        'config_hash': None,
    }

    def _fits(resources, *args, **kwargs):
        del args, kwargs
        return 'H100' in resources.accelerators

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_cluster_from_name',
                           return_value=record), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name',
                              return_value=None), \
            mock.patch.object(resources_lib.Resources,
                              'less_demanding_than',
                              autospec=True,
                              side_effect=_fits), \
            mock.patch.object(backend,
                              '_handle_resize_pre_provision') as pre_provision:
        config = backend._check_existing_cluster(  # pylint: disable=protected-access
            task,
            None,
            'c',
            dryrun=True,
            resize=True)

    # The requested count comes from the hardware-matching candidate.
    with pytest.raises(ValueError, match="different node counts"):
        _ = task.num_nodes
    assert config.num_nodes == 2
    assert pre_provision.call_args.args[1] == 2


@pytest.mark.parametrize('fallback_count', [None, 1, 2])
def test_ordered_smaller_candidate_survives_blocked_four_nodes(fallback_count):
    with dag_lib.Dag() as dag:
        large = resources_lib.Resources(infra='aws/us-east-1',
                                        instance_type='m5.large',
                                        num_nodes=4)
        small = large.copy(num_nodes=fallback_count)
        task = task_lib.Task().set_resources([large, small])

    def feasible(self, resources, num_nodes):
        return resources_utils.FeasibleResources(
            [resources.copy(num_nodes=None)], [], None)

    with mock.patch.object(optimizer, '_check_specified_clouds'), \
            mock.patch.object(optimizer.sky_check,
                              'get_cached_enabled_clouds_or_refresh',
                              return_value=[large.cloud]), \
            mock.patch.object(clouds.AWS, 'get_feasible_launchable_resources', feasible), \
            mock.patch.object(resources_lib.Resources, 'validate'), \
            mock.patch.object(resources_lib.Resources, 'get_cost', return_value=1), \
            mock.patch.object(resources_utils, 'need_to_query_reservations', return_value=False), \
            mock.patch.object(resources_utils, 'make_launchables_for_valid_region_zones',
                              side_effect=lambda r: [r]):
        # Operational code must not consult the deprecated task accessor.
        def legacy_getter(_):
            raise AssertionError('Operational read of Task.num_nodes')

        with mock.patch.object(task_lib.Task, 'num_nodes',
                               task_lib.Task.num_nodes.getter(legacy_getter)):
            optimizer.Optimizer.optimize(dag,
                                         blocked_resources=[large],
                                         quiet=True)
    assert task.best_resources.num_nodes == (fallback_count or 1)


@pytest.mark.parametrize('count_on_resources', [True, False])
def test_autodown_race_keeps_count_of_matching_candidate(count_on_resources):
    """A cluster created with task-level count has no count on its resources."""
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large')
    task = task_lib.Task(
        num_nodes=None if count_on_resources else 4).set_resources(
            launched.copy(num_nodes=4 if count_on_resources else None))
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle,
                            launched_resources=launched,
                            launched_nodes=4,
                            cluster_name='review-race')
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_ever_up': True,
        'config_hash': None
    }
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_cluster_from_name', return_value=record), \
            mock.patch.object(cloud_vm_ray_backend.backend_utils,
                              'refresh_cluster_record', return_value=None), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name', return_value=None):
        config = backend._check_existing_cluster(task, None, 'review-race')
    assert config.num_nodes == 4


def test_resources_keeps_existing_positional_use_spot_argument():
    resource = resources_lib.Resources(None, None, None, None, None, None, None,
                                       True)
    assert resource.use_spot is True


def test_legacy_builder_order_and_shared_resource_isolation():
    shared = resources_lib.Resources(cpus=2)
    task = task_lib.Task(num_nodes=4).set_resources(shared)
    other = task_lib.Task().set_resources(shared)
    assert next(iter(task.resources)).num_nodes == 4
    assert next(iter(other.resources)).num_nodes == shared.num_nodes == 1
    task.set_resources(resources_lib.Resources(memory=16))
    assert next(iter(task.resources)).num_nodes == 4
    task.num_nodes = 2
    assert next(iter(task.resources)).num_nodes == 2
    task.num_nodes = None
    assert next(iter(task.resources)).num_nodes == 1


def test_bound_resource_reused_in_a_new_task_keeps_its_count():
    original = task_lib.Task(num_nodes=4).set_resources(
        resources_lib.Resources(cpus=2))
    resource = next(iter(original.resources))
    # Standalone Resources serialization describes its effective count.
    restored_resource = next(
        iter(resources_lib.Resources.from_yaml_config(
            resource.to_yaml_config())))
    assert restored_resource.num_nodes == 4
    # The new task no longer has the original task's legacy input.
    copied = task_lib.Task().set_resources(resource)
    restored_task = _task_from_config(copied.to_yaml_config())
    assert next(iter(restored_task.resources)).num_nodes == 4
    # The original task retains its old-server-compatible YAML form.
    assert original.to_yaml_config()['num_nodes'] == 4
    assert 'num_nodes' not in original.to_yaml_config()['resources']


@pytest.mark.parametrize('count', [None, 1, 4])
def test_legacy_requests_keep_old_server_yaml_and_version_gate(count):
    task = task_lib.Task(num_nodes=count).set_resources(
        resources_lib.Resources(cpus=2))
    dag = dag_utils.convert_entrypoint_to_dag(task)
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=69):
        sdk._check_resources_num_nodes_api_version(dag)
    config = task.to_yaml_config()
    assert 'num_nodes' not in config['resources']
    assert config.get('num_nodes', 1) == (count or 1)
    restored = _task_from_config(config)
    assert next(iter(restored.resources)).num_nodes == (count or 1)


def test_restored_legacy_task_lowers_count_into_resources():
    original = task_lib.Task()
    state = original.__dict__.copy()
    state['_num_nodes'] = 4
    state['resources'] = {resources_lib.Resources(cpus=2)}
    restored = task_lib.Task.__new__(task_lib.Task)
    restored.__setstate__(state)
    assert next(iter(restored.resources)).num_nodes == 4
    cloned = pickle.loads(pickle.dumps(restored))
    assert next(iter(cloned.resources)).num_nodes == 4
    assert cloned.to_yaml_config()['num_nodes'] == 4
    assert 'num_nodes' not in cloned.to_yaml_config()['resources']


def test_normalized_defaults_do_not_turn_generic_blocks_into_counted_blocks():
    resource = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large')
    candidate = next(
        iter(task_lib.Task(num_nodes=1).set_resources(resource).resources))
    # Both generic and explicitly one-node blocks block this one-node request.
    assert candidate.should_be_blocked_by(resource)
    assert candidate.should_be_blocked_by(resource.copy(num_nodes=1))
    # A larger placement failure allows this smaller concrete request.
    assert not candidate.should_be_blocked_by(resource.copy(num_nodes=4))
    # Generic blocks continue to apply to all sizes.
    assert candidate.copy(num_nodes=4).should_be_blocked_by(resource)


@pytest.mark.parametrize('slices', [1, 2])
def test_resource_count_keeps_tpu_slice_units_in_provisioning(slices):
    resource = resources_lib.Resources(infra='k8s/ctx',
                                       accelerators='tpu-v6e-16',
                                       num_nodes=slices)
    task = task_lib.Task().set_resources(resource)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_cluster_from_name', return_value=None), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name', return_value=None):
        config = backend._check_existing_cluster(task,
                                                 resource,
                                                 'slice-count',
                                                 dryrun=True)
    assert config.resources.num_nodes == slices
    assert config.num_nodes == slices * kubernetes_utils.tpu_vms_per_slice(
        resource)


@pytest.mark.parametrize('ordered,resize,counts,expected', [
    (True, False, [1, 4], 1),
    (False, False, [1, 4], 4),
    (True, True, [4, 2], 4),
    (False, True, [4, 2], 2),
])
def test_existing_cluster_count_selection_is_deterministic(
        ordered, resize, counts, expected):
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large')
    candidates = [launched.copy(num_nodes=n) for n in counts]
    task = task_lib.Task().set_resources(
        candidates if ordered else set(candidates))
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle,
                            launched_resources=launched,
                            launched_nodes=4,
                            cluster_name='count-policy')
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_status_from_cluster_name',
                           return_value=None):
        chosen = cloud_vm_ray_backend.CloudVmRayBackend(
        ).check_resources_fit_cluster(handle, task, skip_num_nodes_check=resize)
    assert chosen.num_nodes == expected
