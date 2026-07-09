"""Unit tests for sky.jobs.recovery_strategy helpers."""

import asyncio

from sky import clouds
from sky import dag as dag_lib
from sky import resources as resources_lib
from sky import task as task_lib
from sky.jobs import recovery_strategy


def test_is_oom_failure_detects_oomkilled():
    exc = RuntimeError(
        'Failed to run setup commands on an instance. (exit code 1). '
        'Pod p terminated: OOMKilled (exit code 137).')
    assert recovery_strategy._is_oom_failure(exc) is True


def test_is_oom_failure_detects_out_of_memory_phrase():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('The container ran out of memory.')) is True


def test_is_oom_failure_is_case_insensitive():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('reason: oomkilled')) is True


def test_is_oom_failure_false_for_unrelated():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('/bin/bash: line 1: conda: command not found')) is False


def _make_eager_executor(task, launched_resources):
    """Build an EagerFailoverStrategyExecutor without running __init__.

    recover() only touches `dag`, `_launched_resources`, `_launch` and
    `_cleanup_cluster`, so we skip the heavy constructor (backend, locks)
    and wire up just those pieces.
    """
    executor = recovery_strategy.EagerFailoverStrategyExecutor.__new__(
        recovery_strategy.EagerFailoverStrategyExecutor)
    dag = dag_lib.Dag()
    dag.add(task)
    executor.dag = dag
    executor._launched_resources = launched_resources
    executor._cleanup_cluster = lambda: None
    return executor


def test_eager_failover_blocks_preempted_region_when_request_unpinned():
    """Regression test for #10021.

    The user did not pin a region/zone, so after a preemption the
    EAGER_NEXT_REGION strategy must relaunch with the previously launched
    region blocked. The old code guarded on the LAUNCHED resources (which
    always carry a concrete region), so the blocking step never ran.
    """
    task = task_lib.Task(run='echo hi')
    task.set_resources(
        {resources_lib.Resources(cloud=clouds.AWS(), use_spot=True)})
    launched = resources_lib.Resources(cloud=clouds.AWS(),
                                       region='us-east-2',
                                       use_spot=True)
    executor = _make_eager_executor(task, launched)

    blocked_seen = []

    async def fake_launch(*args, **kwargs):
        # Snapshot the block set exactly as the launch would see it.
        blocked_seen.append(task.blocked_resources)
        return 123.0

    executor._launch = fake_launch

    assert asyncio.run(executor.recover()) == 123.0
    # The launch must carry exactly one blocked entry: the previously
    # launched region as a whole (zone left as a wildcard).
    assert len(blocked_seen) == 1
    blocked = blocked_seen[0]
    assert blocked is not None and len(blocked) == 1
    entry = next(iter(blocked))
    assert entry.cloud.is_same_cloud(clouds.AWS())
    assert entry.region == 'us-east-2'
    assert entry.zone is None
    assert entry.use_spot
    # recover() must reset the task once the launch attempt is done.
    assert task.blocked_resources is None


def test_eager_failover_skips_blocking_when_region_pinned():
    """The user pinned the region: blocking it would exclude the entire
    search space, so recover() must skip the blocking step and go straight
    to the unconstrained relaunch."""
    task = task_lib.Task(run='echo hi')
    task.set_resources({
        resources_lib.Resources(cloud=clouds.AWS(),
                                region='us-east-2',
                                use_spot=True)
    })
    launched = resources_lib.Resources(cloud=clouds.AWS(),
                                       region='us-east-2',
                                       use_spot=True)
    executor = _make_eager_executor(task, launched)

    blocked_seen = []

    async def fake_launch(*args, **kwargs):
        blocked_seen.append(task.blocked_resources)
        return 42.0

    executor._launch = fake_launch

    assert asyncio.run(executor.recover()) == 42.0
    # Only the final unconstrained launch should run, with no blocking.
    assert blocked_seen == [None]
