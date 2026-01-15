"""Unit tests for sky.dag."""
import pytest

from sky import dag as dag_lib
from sky import task as task_lib


@pytest.fixture
def empty_dag():
    """Fixture for an empty DAG."""
    with dag_lib.Dag() as dag:
        yield dag


@pytest.fixture
def single_task_dag():
    """Fixture for a DAG with single task."""
    with dag_lib.Dag() as dag:
        task_lib.Task()
        yield dag


@pytest.fixture
def linear_three_task_dag():
    """Fixture for a DAG with three tasks in a linear chain."""
    with dag_lib.Dag() as dag:
        task1 = task_lib.Task()
        task2 = task_lib.Task()
        task3 = task_lib.Task()
        dag.add_edge(task1, task2)
        dag.add_edge(task2, task3)
        yield dag


@pytest.fixture
def branching_dag():
    """Fixture for a DAG with one task branching into two tasks."""
    with dag_lib.Dag() as dag:
        dag._tasks = [task_lib.Task() for _ in range(3)]
        dag.add_edge(dag.tasks[0], dag.tasks[1])
        dag.add_edge(dag.tasks[0], dag.tasks[2])
        yield dag


@pytest.fixture
def merging_dag():
    """Fixture for a DAG with two tasks merging into one task."""
    with dag_lib.Dag() as dag:
        dag._tasks = [task_lib.Task() for _ in range(3)]
        dag.add_edge(dag.tasks[0], dag.tasks[2])
        dag.add_edge(dag.tasks[1], dag.tasks[2])
        yield dag


@pytest.fixture
def multi_parent_dag():
    """Fixture for a DAG with two tasks merging into one task."""
    with dag_lib.Dag() as dag:
        dag._tasks = [task_lib.Task() for _ in range(3)]
        dag.add_edge(dag.tasks[0], dag.tasks[2])
        dag.add_edge(dag.tasks[1], dag.tasks[2])
        yield dag


@pytest.fixture
def diamond_dag():
    """Fixture for a diamond-shaped DAG: A -> (B,C) -> D."""
    with dag_lib.Dag() as dag:
        dag._tasks = [task_lib.Task() for _ in range(4)]
        dag.add_edge(dag.tasks[0], dag.tasks[1])
        dag.add_edge(dag.tasks[0], dag.tasks[2])
        dag.add_edge(dag.tasks[1], dag.tasks[3])
        dag.add_edge(dag.tasks[2], dag.tasks[3])
        yield dag


@pytest.fixture
def cyclic_dag():
    """Fixture for a DAG with a cycle (invalid DAG)."""
    with dag_lib.Dag() as dag:
        task1 = task_lib.Task()
        task2 = task_lib.Task()
        dag.add_edge(task1, task2)
        dag.add_edge(task2, task1)  # Create cycle
        yield dag


@pytest.mark.parametrize('dag_fixture,description', [
    ('empty_dag', 'Empty DAG'),
    ('single_task_dag', 'Single task DAG'),
    ('linear_three_task_dag', 'Linear chain of three tasks'),
])
def test_is_chain_true_cases(request, dag_fixture, description):
    """Test cases where is_chain() should return True."""
    dag = request.getfixturevalue(dag_fixture)
    assert dag.is_chain(), f"Failed for case: {description}"


@pytest.mark.parametrize('dag_fixture,description', [
    ('branching_dag', 'Branching DAG'),
    ('merging_dag', 'Merging DAG'),
    ('diamond_dag', 'Diamond DAG'),
])
def test_is_chain_false_cases(request, dag_fixture, description):
    """Test cases where is_chain() should return False."""
    dag = request.getfixturevalue(dag_fixture)
    assert not dag.is_chain(), f"Failed for case: {description}"


@pytest.mark.parametrize('dag_fixture,description,expected_old,expected_new', [
    ('linear_three_task_dag', 'Linear chain of three tasks', True, True),
    ('multi_parent_dag', 'DAG with two tasks merging into one task', True,
     False),
])
def test_is_chain_regression(request, dag_fixture, description, expected_old,
                             expected_new):
    """Regression test comparing new implementation with old behavior."""
    dag = request.getfixturevalue(dag_fixture)

    def old_is_chain(dag):
        # Old implementation
        is_chain = True
        visited_zero_out_degree = False
        for node in dag.graph.nodes:
            out_degree = dag.graph.out_degree(node)
            if out_degree > 1:
                is_chain = False
                break
            elif out_degree == 0:
                if visited_zero_out_degree:
                    is_chain = False
                    break
                else:
                    visited_zero_out_degree = True
        return is_chain

    assert dag.is_chain() == expected_new, f"Failed for case: {description}"
    assert old_is_chain(dag) == expected_old, f"Failed for case: {description}"


# TODO(andy): Currently cyclic DAGs are not detected and is_chain() simply
# returns False. Once we implement cycle detection that raises an error,
# update this test to use pytest.raises.
@pytest.mark.xfail(reason="Cycle detection not implemented yet")
def test_is_chain_with_cycle(cyclic_dag):
    """Test is_chain() with cyclic graph.
    """
    with pytest.raises(ValueError):
        cyclic_dag.is_chain()
