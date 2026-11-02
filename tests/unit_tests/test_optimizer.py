"""Unit tests for sky.optimizer."""
from unittest import mock

import pytest

from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib


def _optimize_ordered_task_with_mock_launchable(dag,
                                                launchable_call_indexes,
                                                unreachable_call_indexes=()):
    fill_calls = []

    def fake_fill_in_launchable_resources(task, blocked_resources, quiet):
        del blocked_resources, quiet
        requested_resources = next(iter(task.resources))
        call_index = len(fill_calls)
        fill_calls.append(requested_resources)
        launchable_resources = ([requested_resources] if call_index
                                in launchable_call_indexes else [])
        unreachable_errors = ([
            exceptions.KubeAPIUnreachableError(
                f"Kubernetes context 'ctx{call_index}' is unreachable")
        ] if call_index in unreachable_call_indexes else [])
        return ({
            requested_resources: launchable_resources
        }, {}, [], {}, unreachable_errors)

    def fake_optimize_by_dp(topo_order, node_to_cost_map, minimize_cost):
        del node_to_cost_map, minimize_cost
        task_node = topo_order[0]
        return {task_node: next(iter(task_node.resources))}, 0

    with mock.patch('sky.optimizer._fill_in_launchable_resources',
                    side_effect=fake_fill_in_launchable_resources), \
            mock.patch.object(optimizer.Optimizer,
                              '_estimate_nodes_cost_or_time',
                              return_value=({}, {})), \
            mock.patch.object(optimizer.Optimizer,
                              '_optimize_by_dp',
                              side_effect=fake_optimize_by_dp), \
            mock.patch.object(optimizer.Optimizer,
                              '_compute_total_time',
                              return_value=0):
        optimizer.Optimizer._optimize_dag(  # pylint: disable=protected-access
            dag, quiet=True)

    return fill_calls


def test_ordered_resources_without_docker_login_stops_at_first_launchable():
    """Ensure ordered resources stop at the first launchable candidate."""
    with dag_lib.Dag() as dag:
        task = task_lib.Task(name='ordered-no-docker')
        task.set_resources([
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='A100:8',
                                    use_spot=True),
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='L4:4',
                                    use_spot=True),
        ])

    fill_calls = _optimize_ordered_task_with_mock_launchable(
        dag, launchable_call_indexes={0})

    assert len(fill_calls) == 1
    assert task.best_resources.accelerators == {'A100': 8}


def test_ordered_resources_with_docker_login_stops_at_first_launchable():
    """Ensure ordered resources use the post-set_resources launchable key."""
    with dag_lib.Dag() as dag:
        task = task_lib.Task(
            name='ordered-docker',
            secrets={
                'SKYPILOT_DOCKER_SERVER': 'registry.example.com',
                'SKYPILOT_DOCKER_USERNAME': 'user',
                'SKYPILOT_DOCKER_PASSWORD': 'password',
            })
        task.set_resources([
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='A100:8',
                                    image_id='docker:repo/image:tag',
                                    use_spot=True),
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='L4:4',
                                    image_id='docker:repo/image:tag',
                                    use_spot=True),
        ])

    fill_calls = _optimize_ordered_task_with_mock_launchable(
        dag, launchable_call_indexes={0})

    assert len(fill_calls) == 1
    assert task.best_resources.accelerators == {'A100': 8}


def test_ordered_resources_with_docker_login_uses_first_launchable():
    """Ensure ordered resources continue to the first launchable candidate."""
    with dag_lib.Dag() as dag:
        task = task_lib.Task(
            name='ordered-docker-first-unavailable',
            secrets={
                'SKYPILOT_DOCKER_SERVER': 'registry.example.com',
                'SKYPILOT_DOCKER_USERNAME': 'user',
                'SKYPILOT_DOCKER_PASSWORD': 'password',
            })
        task.set_resources([
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='A100:8',
                                    image_id='docker:repo/image:tag',
                                    use_spot=True),
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='H100:8',
                                    image_id='docker:repo/image:tag',
                                    use_spot=True),
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='L4:4',
                                    image_id='docker:repo/image:tag',
                                    use_spot=True),
        ])

    fill_calls = _optimize_ordered_task_with_mock_launchable(
        dag, launchable_call_indexes={1})

    assert len(fill_calls) == 2
    assert task.best_resources.accelerators == {'H100': 8}


def _unreachable(context):
    return exceptions.KubeAPIUnreachableError(
        f"Kubernetes context '{context}' is unreachable: Timed out")


def _optimize_with_mock_fill_in(dag, fill_in_results):
    """Runs the optimizer; each fill-in call returns the next result.

    A result is (launchable, fuzzy_candidates, unreachable_errors) for the
    task's only requested resources.
    """
    results = iter(fill_in_results)

    def fake_fill_in_launchable_resources(task, blocked_resources, quiet):
        del blocked_resources, quiet
        requested_resources = next(iter(task.resources))
        launchable, fuzzy_candidates, unreachable_errors = next(results)
        launchable_resources = [requested_resources] if launchable else []
        return ({
            requested_resources: launchable_resources
        }, {}, list(fuzzy_candidates), {}, list(unreachable_errors))

    with mock.patch('sky.optimizer._check_specified_clouds'), \
            mock.patch('sky.optimizer._fill_in_launchable_resources',
                       side_effect=fake_fill_in_launchable_resources), \
            mock.patch('sky.optimizer.sky_check.'
                       'get_cached_enabled_clouds_or_refresh',
                       return_value=[clouds.Kubernetes()]):
        optimizer.Optimizer.optimize(dag, quiet=True)


def _single_task_dag():
    with dag_lib.Dag() as dag:
        task = task_lib.Task(name='no-launchable')
        task.set_resources(resources_lib.Resources(cpus='4+'))
    return dag


def _ordered_task(name):
    with dag_lib.Dag() as dag:
        task = task_lib.Task(name=name)
        task.set_resources([
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='A100:8',
                                    use_spot=True),
            resources_lib.Resources(infra='aws/us-east-2',
                                    accelerators='L4:4',
                                    use_spot=True),
        ])
    return dag, task


def test_unreachable_infra_is_reported_as_failover_history():
    """Unreachable infra makes the failure retryable, not infeasible."""
    unreachable = _unreachable('ctx1')

    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _optimize_with_mock_fill_in(_single_task_dag(),
                                    [(False, [], [unreachable])])

    assert e.value.failover_history == [unreachable]
    assert 'No reachable infra has resources' in str(e.value)
    assert "'ctx1' is unreachable" in str(e.value)
    assert 'does not contain any instances' not in str(e.value)


def test_unreachable_infra_keeps_the_usual_hints():
    """The accelerator candidates and hints are kept next to the outage."""
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _optimize_with_mock_fill_in(
            _single_task_dag(), [(False, ['A100:1'], [_unreachable('ctx1')])])

    assert 'Try one of these offered accelerators' in str(e.value)
    assert 'sky gpus list' in str(e.value)
    assert "'ctx1' is unreachable" in str(e.value)


def test_unreachable_errors_are_deduplicated():
    """A context checked once per requested resource is reported once."""
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _optimize_with_mock_fill_in(
            _single_task_dag(),
            [(False, [], [_unreachable('ctx1'),
                          _unreachable('ctx1')])])

    assert len(e.value.failover_history) == 1
    assert str(e.value).count("'ctx1' is unreachable") == 1


def test_infeasible_request_has_no_failover_history():
    """With every candidate reachable, no fit stays a terminal failure."""
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _optimize_with_mock_fill_in(_single_task_dag(), [(False, [], [])])

    assert not e.value.failover_history
    assert 'does not contain any instances' in str(e.value)


def test_ordered_resources_unreachable_then_infeasible_is_retryable():
    """An earlier unreachable choice keeps the failure retryable."""
    dag, _ = _ordered_task('ordered-unreachable-then-infeasible')

    # The prepass tries both choices; the estimate checks the last one again.
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _optimize_with_mock_fill_in(dag, [
            (False, [], [_unreachable('ctx0')]),
            (False, [], []),
            (False, [], []),
        ])

    assert len(e.value.failover_history) == 1
    assert "'ctx0' is unreachable" in str(e.value)


def test_ordered_resources_unreachable_then_launchable_uses_later_choice():
    """An unreachable earlier choice does not block a launchable later one."""
    dag, task = _ordered_task('ordered-unreachable-then-launchable')

    fill_calls = _optimize_ordered_task_with_mock_launchable(
        dag, launchable_call_indexes={1}, unreachable_call_indexes={0})

    assert len(fill_calls) == 2
    assert task.best_resources.accelerators == {'L4': 4}
