"""Unit tests for sky.optimizer."""
from unittest import mock

from sky import dag as dag_lib
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib


def _optimize_ordered_task_with_mock_launchable(dag, launchable_call_indexes):
    fill_calls = []

    def fake_fill_in_launchable_resources(task, blocked_resources, quiet):
        del blocked_resources, quiet
        requested_resources = next(iter(task.resources))
        call_index = len(fill_calls)
        fill_calls.append(requested_resources)
        launchable_resources = ([requested_resources] if call_index
                                in launchable_call_indexes else [])
        return {requested_resources: launchable_resources}, {}, [], {}

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
