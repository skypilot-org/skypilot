"""Unit tests for `num_nodes` set on resources (e.g., per any_of candidate)."""
import asyncio
import pickle
import threading
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
from sky.utils.cli_utils import status_utils


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
@pytest.mark.parametrize('header', [False, True])
def test_cli_num_nodes_overrides_where_set(tmp_path, resources_config, expected,
                                           header):
    yaml_path = tmp_path / 'task.yaml'
    yaml_path.write_text(
        dag_utils.yaml_utils.dump_yaml_str({
            'resources': resources_config,
            'run': 'echo hi'
        }))
    if header:
        yaml_path.write_text('name: pipeline\n---\n' + yaml_path.read_text())
    task = command._make_task_or_dag_from_entrypoint_with_overrides(  # pylint: disable=protected-access
        entrypoint=(str(yaml_path),),
        num_nodes=4)
    assert [r.num_nodes for r in task.resources] == expected
    # A legacy task count stays at the task-level wire boundary.
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
    assert 'num_nodes' not in r.copy(num_nodes=None).to_yaml_config()
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
    assert 'num_nodes' not in restored.to_yaml_config()


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
    # An omitted request count means one; an omitted block count is generic.
    assert not res(None).should_be_blocked_by(res(4))
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
    # The successful launch records the matched request, including its
    # legacy-input provenance. Recovery only changes placement fields.
    matched = next(r for r in task.resources if r.num_nodes == 2)
    executor._launched_resources = matched.copy(  # pylint: disable=protected-access
        cloud=clouds.AWS(),
        region='us-east-1',
        instance_type='p5.48xlarge')
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


def test_task_resources_str_pairs_node_counts_with_accelerators():
    task = task_lib.Task().set_resources({
        resources_lib.Resources(accelerators='B200:8', num_nodes=2),
        resources_lib.Resources(accelerators='H100:8', num_nodes=4),
    })
    assert backend_utils.get_task_resources_str(task) == (
        '2x[B200:8], 4x[H100:8]')
    task.best_resources = next(r for r in task.resources if r.num_nodes == 4)
    assert backend_utils.get_task_resources_str(task) == '4x[H100:8]'


def test_task_resources_str_keeps_uniform_legacy_alternatives_compact():
    task = task_lib.Task(num_nodes=3).set_resources({
        resources_lib.Resources(accelerators='H100:8'),
        resources_lib.Resources(accelerators='A100:8'),
    })
    assert backend_utils.get_task_resources_str(task) in {
        '3x[A100:8, H100:8]', '3x[H100:8, A100:8]'
    }


def test_task_resources_str_preserves_ordered_count_preferences():
    task = task_lib.Task().set_resources([
        resources_lib.Resources(accelerators='H100:8', num_nodes=4),
        resources_lib.Resources(accelerators='B200:8', num_nodes=2),
    ])
    assert backend_utils.get_task_resources_str(task) == (
        '4x[H100:8], 2x[B200:8]')


def test_task_resources_str_does_not_merge_same_gpu_at_different_counts():
    task = task_lib.Task().set_resources({
        resources_lib.Resources(accelerators='H100:8', num_nodes=2),
        resources_lib.Resources(accelerators='H100:8', num_nodes=4),
    })
    assert backend_utils.get_task_resources_str(task) == (
        '2x[H100:8], 4x[H100:8]')


def test_task_resources_str_cpu_candidates_and_spot_counts():
    task = task_lib.Task().set_resources({
        resources_lib.Resources(cpus=4, num_nodes=2),
        resources_lib.Resources(cpus=8, num_nodes=4, use_spot=True),
    })
    assert backend_utils.get_task_resources_str(
        task, is_managed_job=True) == ('2x[CPU:4], 4x[CPU:8][Spot]')
    task.best_resources = next(r for r in task.resources if r.num_nodes == 4)
    assert backend_utils.get_task_resources_str(
        task, is_managed_job=True) == ('4x[CPU:8][Spot]')


def test_task_resources_str_uniform_cpu_minimum_and_spot_summary():
    task = task_lib.Task(num_nodes=2).set_resources({
        resources_lib.Resources(cpus=4),
        resources_lib.Resources(cpus=8, use_spot=True),
    })
    assert backend_utils.get_task_resources_str(
        task, is_managed_job=True) == ('2x[CPU:4.0+][On-demand|Spot]')


def test_resources_repr_includes_inherited_count_and_invalidates_cached_repr():
    resource = resources_lib.Resources(accelerators='H100:8')
    original_repr = repr(resource)
    task = task_lib.Task(num_nodes=4).set_resources(resource)
    assert 'num_nodes=4' in repr(next(iter(task.resources)))
    assert 'num_nodes=4' in repr(task.resources)
    assert 'num_nodes=4' in repr(task)
    assert repr(resource) == original_repr
    assert 'num_nodes=4' in repr(task_lib.Task(num_nodes=4))


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
        return resources_utils.FeasibleResources([resources.copy()], [], None)

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


@pytest.mark.parametrize('resize,expected_capacity', [(False, 4), (True, 2)])
def test_existing_cluster_provision_plan_retains_its_actual_size(
        resize, expected_capacity):
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large')
    requested = launched.copy(num_nodes=2)
    task = task_lib.Task().set_resources(requested)
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle,
                            launched_resources=launched,
                            launched_nodes=4,
                            cluster_name='capacity-count')
    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_ever_up': True,
        'config_hash': None
    }
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_cluster_from_name', return_value=record), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name', return_value=None), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_cluster_yaml_str', return_value=None), \
            mock.patch.object(backend, '_handle_resize_pre_provision'):
        config = backend._check_existing_cluster(task,
                                                 None,
                                                 'capacity-count',
                                                 dryrun=True,
                                                 resize=resize)
    assert config.num_nodes == expected_capacity
    assert config.resources.num_nodes == expected_capacity
    assert next(iter(task.resources)).num_nodes == 2


@pytest.mark.parametrize('on_task', [False, True])
def test_recovery_remembers_requested_count_on_a_larger_cluster(on_task):
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large',
                                       num_nodes=4)
    task = task_lib.Task(num_nodes=2 if on_task else None).set_resources(
        launched.copy(num_nodes=None if on_task else 2))
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle,
                            launched_resources=launched,
                            launched_nodes=4,
                            launched_task_nodes=4,
                            cached_cluster_info=None,
                            cluster_name='recover-count')
    executor = recovery_strategy.FailoverStrategyExecutor.__new__(
        recovery_strategy.FailoverStrategyExecutor)
    executor.cluster_name = 'recover-count'
    executor.job_id = 1
    executor.dag = mock.MagicMock(tasks=[task])
    loop_thread = threading.get_ident()
    match_request = backend_utils.get_task_resources_for_cluster
    copy_launched = launched.copy

    def match_in_thread(*args):
        assert threading.get_ident() != loop_thread
        return match_request(*args)

    def copy_in_thread(**kwargs):
        assert threading.get_ident() != loop_thread
        return copy_launched(**kwargs)

    with mock.patch.object(recovery_strategy.StrategyExecutor, '_launch',
                           new=mock.AsyncMock(return_value=1.0)), \
            mock.patch.object(recovery_strategy.global_user_state,
                              'get_handle_from_cluster_name', return_value=handle), \
            mock.patch.object(cloud_vm_ray_backend.global_user_state,
                              'get_status_from_cluster_name',
                              side_effect=AssertionError('Unexpected state read')), \
            mock.patch.object(recovery_strategy.state, 'set_job_infra'), \
            mock.patch.object(backend_utils, 'get_task_resources_for_cluster',
                              side_effect=match_in_thread), \
            mock.patch.object(launched, 'copy', side_effect=copy_in_thread):
        asyncio.run(executor._launch())
    assert executor._launched_resources.num_nodes == 2


@pytest.mark.parametrize('legacy_count', [None, 1, 4])
def test_single_field_builder_reuse_and_default_round_trip(legacy_count):
    task = task_lib.Task(num_nodes=legacy_count).set_resources(
        resources_lib.Resources(cpus=2))
    bound = next(iter(task.resources))
    # Reapplying the task's own resource is safe without provenance flags.
    task.set_resources(bound)
    if legacy_count is not None:
        task.set_resources(resources_lib.Resources(num_nodes=legacy_count))
    restored = _task_from_config(task.to_yaml_config())
    assert next(iter(restored.resources)).num_nodes == (legacy_count or 1)
    dag = dag_utils.convert_entrypoint_to_dag(restored)
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=69):
        sdk._check_resources_num_nodes_api_version(dag)
    if legacy_count is None:
        assert 'num_nodes' not in bound.to_yaml_config()
        assert 'num_nodes' not in restored.get_resource_config()


def test_legacy_and_matching_resource_count_in_sdk():
    resource = resources_lib.Resources(cpus=2, num_nodes=4)
    task = task_lib.Task(num_nodes=4, resources=resource)
    task.set_resources_override({'memory': 16})
    assert next(iter(task.resources)).num_nodes == 4
    assert task.to_yaml_config()['num_nodes'] == 4
    assert 'num_nodes' not in task.to_yaml_config()['resources']
    # Strict YAML validation still rejects specifying the count twice.
    with pytest.raises(ValueError, match='Set it in one place only'):
        _task_from_config({'num_nodes': 4, 'resources': {'num_nodes': 4}})


@pytest.mark.parametrize('source', ['constructor', 'setter', 'getter', 'yaml'])
def test_legacy_count_deprecation_warns_once_at_user_input(source):
    task_lib.warn_legacy_num_nodes.cache_clear()
    try:
        with mock.patch.object(task_lib.logger, 'warning') as warning:
            for _ in range(2):
                if source == 'constructor':
                    task_lib.Task(num_nodes=2)
                elif source == 'yaml':
                    _task_from_config({'num_nodes': 2})
                else:
                    task = task_lib.Task()
                    if source == 'setter':
                        task.num_nodes = 2
                    else:
                        _ = task.num_nodes
            assert warning.call_count == (1 if source
                                          in {'constructor', 'getter'} else 0)
    finally:
        task_lib.warn_legacy_num_nodes.cache_clear()


@pytest.mark.parametrize('legacy_yaml', [False, True])
@pytest.mark.parametrize('header', [False, True])
def test_cli_warns_for_legacy_yaml_but_not_the_node_count_flag(
        tmp_path, legacy_yaml, header):
    config = {'resources': {'cpus': 2}, 'run': 'echo hi'}
    if legacy_yaml:
        config['num_nodes'] = 2
    path = tmp_path / 'nodes.yaml'
    path.write_text(dag_utils.yaml_utils.dump_yaml_str(config))
    if header:
        path.write_text('name: pipeline\n---\n' + path.read_text())
    task_lib.warn_legacy_num_nodes.cache_clear()
    try:
        with mock.patch.object(task_lib.logger, 'warning') as warning:
            for _ in range(2):
                task = command._make_task_or_dag_from_entrypoint_with_overrides(
                    entrypoint=(str(path),), num_nodes=4)
            assert next(iter(task.resources)).num_nodes == 4
            assert warning.call_count == (1 if legacy_yaml else 0)
    finally:
        task_lib.warn_legacy_num_nodes.cache_clear()


@pytest.mark.parametrize('stored_count', [None, 4])
def test_cluster_count_is_rendered_once_for_old_and_new_handles(stored_count):
    resource = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large',
                                       num_nodes=stored_count)
    handle = mock.MagicMock(launched_resources=resource,
                            launched_nodes=4,
                            launched_task_nodes=4,
                            cluster_name='count-display')
    handle_str = cloud_vm_ray_backend.CloudVmRayResourceHandle.__repr__(handle)
    cost_str = status_utils._get_resources_for_cost_report({
        'resources': resource,
        'num_nodes': 4
    })
    for display in [handle_str, cost_str]:
        assert display.count('num_nodes=4') == 1
        assert '4x ' not in display
    task = task_lib.Task().set_resources(resource.copy(num_nodes=8))
    with mock.patch.object(cloud_vm_ray_backend.global_user_state,
                           'get_status_from_cluster_name',
                           return_value=None):
        with pytest.raises(exceptions.ResourcesMismatchError) as error:
            cloud_vm_ray_backend.CloudVmRayBackend(
            ).check_resources_fit_cluster(handle, task)
    existing = str(error.value).split('Existing:')[1]
    assert existing.count('num_nodes=4') == 1
    assert '4x ' not in existing


def test_setup_only_exec_preserves_matching_count_and_clears_demands():
    second = resources_lib.Resources(cpus=2, num_nodes=4)
    task = task_lib.Task(setup='echo setup',
                         resources=[
                             resources_lib.Resources(accelerators='H100:8',
                                                     num_nodes=2), second
                         ])
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large',
                                       num_nodes=4)
    handle = mock.MagicMock(launched_resources=launched, launched_nodes=4)
    handle.provision_runtime_metadata.run_started = False
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend._setup_cmd = 'echo setup'
    with mock.patch.object(backend, 'check_resources_fit_cluster',
                           side_effect=AssertionError('Unexpected hardware check')), \
            mock.patch.object(backend, '_add_job', return_value=(1, 'log-dir')), \
            mock.patch.object(backend, '_execute_task_n_nodes') as execute, \
            mock.patch.object(backend, '_record_job_spec'):
        handle.num_ips_per_node = 1
        assert backend._execute(handle, task) == 1
    submitted = execute.call_args.args[1]
    selected = next(iter(submitted.resources))
    assert selected.num_nodes == 4
    assert selected.accelerators is None


def test_handle_repr_with_missing_resources():
    handle = mock.MagicMock(launched_resources=None, launched_task_nodes=2)
    assert 'launched_resources=2x None' in (
        cloud_vm_ray_backend.CloudVmRayResourceHandle.__repr__(handle))


@pytest.mark.parametrize('count', [None, 1, 4])
def test_legacy_stored_job_resources_keep_the_old_schema(count):
    task = task_lib.Task(num_nodes=count).set_resources(
        [resources_lib.Resources(cpus=2),
         resources_lib.Resources(cpus=4)])
    config = task.get_resource_config()
    assert 'num_nodes' not in config
    assert all('num_nodes' not in r for r in config.get('ordered', []))


def test_recovery_unexpected_match_failure_keeps_launched_resources():
    launched = resources_lib.Resources(infra='aws/us-east-1',
                                       instance_type='m5.large',
                                       num_nodes=4)
    task = task_lib.Task().set_resources(launched.copy(num_nodes=2))
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle,
                            launched_resources=launched,
                            launched_nodes=4,
                            cached_cluster_info=None)
    executor = recovery_strategy.FailoverStrategyExecutor.__new__(
        recovery_strategy.FailoverStrategyExecutor)
    executor.cluster_name = 'recover-count'
    executor.job_id = 1
    executor.dag = mock.MagicMock(tasks=[task])
    with mock.patch.object(recovery_strategy.StrategyExecutor, '_launch',
                           new=mock.AsyncMock(return_value=1.0)), \
            mock.patch.object(recovery_strategy.global_user_state,
                              'get_handle_from_cluster_name', return_value=handle), \
            mock.patch.object(backend_utils, 'get_task_resources_for_cluster',
                              return_value=None), \
            mock.patch.object(recovery_strategy.state, 'set_job_infra'):
        assert asyncio.run(executor._launch()) == 1.0
    assert executor._launched_resources is launched
