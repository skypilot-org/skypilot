"""Blocked resources in the launch context reach every optimize call."""
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import execution
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib
from sky.jobs import constants as managed_job_constants
from sky.jobs import recovery_strategy
from sky.utils import resources_utils

_KEY = managed_job_constants.BLOCKED_RESOURCES_LAUNCH_CONTEXT_KEY


def _task(resources):
    task = task_lib.Task(run='echo hi')
    task.set_resources(resources)
    dag = dag_lib.Dag()
    dag.add(task)
    return dag


def _run(extra_launch_context, dag=None):
    """Runs _execute_dag's OPTIMIZE and PROVISION stages. Returns the task the
    provisioner got, after the backend has also called the planner."""
    if dag is None:
        dag = _task(resources_lib.Resources(cloud=clouds.AWS()))
    provisioned = []

    def fake_provision(task, *args, **kwargs):
        del args, kwargs
        # What the backend does under the cluster lock.
        backend.register_info.call_args.kwargs['planner'](task)
        provisioned.append(task)
        return None, False

    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    backend.provision.side_effect = fake_provision
    execution._execute_dag(
        dag,
        dryrun=True,
        stream_logs=False,
        handle=None,
        backend=backend,
        retry_until_up=False,
        optimize_target=mock.MagicMock(),
        stages=[execution.Stage.OPTIMIZE, execution.Stage.PROVISION],
        cluster_name=None,
        detach_setup=False,
        no_setup=False,
        clone_disk_from=None,
        skip_unnecessary_provisioning=False,
        resize=False,
        _quiet_optimizer=True,
        _is_launched_by_jobs_controller=True,
        _is_launched_by_sky_serve_controller=False,
        _extra_launch_context=extra_launch_context)
    [task] = provisioned
    return task


def _spy_optimize(fail_when_blocked=False):
    """Records each optimize call's blocked resources."""
    seen = []

    def fake_optimize(dag, **kwargs):
        blocked = kwargs.get('blocked_resources')
        seen.append(blocked)
        if fail_when_blocked and blocked:
            raise exceptions.ResourcesUnavailableError('Nothing else fits.')
        dag.tasks[0].best_resources = resources_lib.Resources(
            infra='aws/us-west-2', instance_type='g6.xlarge')
        return dag

    return seen, mock.patch.object(execution.optimizer.Optimizer,
                                   'optimize',
                                   side_effect=fake_optimize)


def _block_config():
    return resources_lib.Resources(infra='aws/us-east-1',
                                   instance_type='g6.xlarge',
                                   use_spot=True).to_yaml_config()


def test_blocked_resources_reach_optimizer_planner_and_provisioner(
        enable_all_clouds):
    seen, patched = _spy_optimize()
    with patched:
        task = _run({_KEY: [_block_config()]})

    # The first optimize, then the planner, then the provisioner's own list.
    assert len(seen) == 2
    for blocked in seen + [task.blocked_resources]:
        [parsed] = blocked
        assert (parsed.region, parsed.instance_type,
                parsed.use_spot) == ('us-east-1', 'g6.xlarge', True)


def test_no_block_without_the_key(enable_all_clouds):
    seen, patched = _spy_optimize()
    with patched:
        task = _run({})
    assert seen == [None, None] and task.blocked_resources is None


# An instance type the server's catalog no longer has.
_UNPARSEABLE = {
    'infra': 'aws/us-east-1',
    'instance_type': 'zz9.gone',
    'memory': 16,
    'use_spot': True
}


@pytest.mark.parametrize('configs,applied', [
    pytest.param(lambda: [_UNPARSEABLE], 0, id='only-unparseable'),
    pytest.param(
        lambda: [_UNPARSEABLE, _block_config()], 1, id='unparseable-and-valid'),
])
def test_unparseable_block_is_ignored(enable_all_clouds, configs, applied):
    seen, patched = _spy_optimize()
    with patched:
        task = _run({_KEY: configs()})
    assert [len(b or []) for b in seen] == [applied, applied]
    assert len(task.blocked_resources or []) == applied


def test_block_is_dropped_when_nothing_else_fits(enable_all_clouds):
    seen, patched = _spy_optimize(fail_when_blocked=True)
    with patched:
        task = _run({_KEY: [_block_config()]})

    # Blocked attempt, unblocked retry, then an unblocked planner; and the
    # provisioner must not skip the region either.
    assert [b is not None for b in seen] == [True, False, False]
    assert task.blocked_resources is None


# Lazy: building a block reads the catalog, so not at collection time.
@pytest.mark.parametrize('context,optimize_calls', [
    pytest.param(
        lambda: {_KEY: [_block_config()]}, 2, id='blocked-then-unblocked'),
    pytest.param(lambda: {}, 1, id='no-block-no-retry'),
])
def test_real_infeasibility_still_fails(enable_all_clouds, context,
                                        optimize_calls):

    def infeasible(dag, **kwargs):
        raise exceptions.ResourcesUnavailableError('No such GPU.')

    with mock.patch.object(execution.optimizer.Optimizer,
                           'optimize',
                           side_effect=infeasible) as optimize:
        with pytest.raises(exceptions.ResourcesUnavailableError):
            _run(context())
    assert optimize.call_count == optimize_calls


@pytest.mark.parametrize('requested,moves', [
    pytest.param(lambda: resources_lib.Resources(
        cloud=clouds.AWS(), accelerators={'L4': 1}, use_spot=True),
                 'region',
                 id='aws-spot-gpu'),
    pytest.param(lambda: resources_lib.Resources(cloud=clouds.AWS(),
                                                 instance_type='g6.xlarge'),
                 'zone',
                 id='aws-on-demand'),
    pytest.param(lambda: {
        resources_lib.Resources(
            infra=f'aws/{region}', accelerators={'L4': 1}, use_spot=True)
        for region in ('us-east-1', 'us-west-2')
    },
                 'region',
                 id='aws-any-of-two-regions'),
    pytest.param(lambda: resources_lib.Resources(
        infra='aws/us-east-1', accelerators={'L4': 1}, use_spot=True),
                 None,
                 id='aws-pinned-region'),
    pytest.param(lambda: resources_lib.Resources(
        cloud=clouds.GCP(), accelerators={'L4': 1}, use_spot=True),
                 'region',
                 id='gcp-spot-gpu'),
    pytest.param(lambda: resources_lib.Resources(
        cloud=clouds.GCP(), accelerators={'tpu-v5litepod-8': 1}),
                 'zone',
                 id='gcp-tpu-on-demand'),
])
def test_recovery_block_end_to_end(enable_all_clouds, requested, moves):
    """The block the controller builds from the launched resources, through
    the launch context and the real optimizer."""
    first = optimizer.Optimizer.optimize(_task(requested()),
                                         quiet=True).tasks[0].best_resources
    if first.zone is None:
        # The zone the provisioner launched in, as the handle records it.
        first = resources_utils.make_launchables_for_valid_region_zones(
            first, override_optimize_by_zone=True)[0]
    block = recovery_strategy._location_block(first)
    assert block is not None

    task = _run({_KEY: [block[1]]}, dag=_task(requested()))

    chosen = task.best_resources
    assert (chosen.cloud.is_same_cloud(first.cloud),
            chosen.use_spot) == (True, first.use_spot)
    if moves is None:
        # The only region allowed: relaunch there instead of failing.
        assert (chosen.region, chosen.instance_type) == (first.region,
                                                         first.instance_type)
        assert task.blocked_resources is None
        return
    # The provisioner gets the block, to skip the zones it covers.
    assert task.blocked_resources is not None
    [sent] = task.blocked_resources
    assert first.should_be_blocked_by(sent)
    assert not chosen.should_be_blocked_by(sent)
    if moves == 'region':
        # Not the instance type: the cheapest one varies by region.
        assert chosen.region != first.region
    else:
        # Only the zone is skipped: another zone of the region still runs it.
        assert first.copy(zone=None).should_be_blocked_by(sent) is False


def test_ordered_regions_recover_in_listed_order(enable_all_clouds):
    """Recovery moves to the first listed region it isn't skipping."""
    regions = ('us-east-1', 'us-east-2', 'us-west-2')

    def ordered():
        return [
            resources_lib.Resources(infra=f'aws/{region}',
                                    accelerators={'L4': 1},
                                    use_spot=True) for region in regions
        ]

    chosen = optimizer.Optimizer.optimize(_task(ordered()),
                                          quiet=True).tasks[0].best_resources
    assert chosen.region == regions[0]
    blocks = []
    for expected in regions[1:]:
        # The controller skips every region the job recently failed in.
        blocks.append(recovery_strategy._location_block(chosen)[1])
        chosen = _run({_KEY: blocks}, dag=_task(ordered())).best_resources
        assert chosen.region == expected
