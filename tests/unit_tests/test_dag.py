"""Unit tests for sky.dag."""
import pytest

import sky


@pytest.fixture
def empty_dag():
    """Fixture for an empty DAG."""
    with sky.Dag() as dag:
        yield dag


@pytest.fixture
def single_task_dag():
    """Fixture for a DAG with single task."""
    with sky.Dag() as dag:
        sky.Task()
        yield dag


@pytest.mark.parametrize('dag_fixture', [
    'empty_dag',
    'single_task_dag',
])
def test_is_chain_simple_cases(request, dag_fixture):
    """Test is_chain() with simple DAGs that should return True."""
    dag = request.getfixturevalue(dag_fixture)
    assert dag.is_chain(), f"DAG should be a chain: {dag}"


def test_is_chain_linear():
    """Test is_chain() with a linear chain of tasks."""
    with sky.Dag() as dag:
        task1 = sky.Task()
        task2 = sky.Task()
        task3 = sky.Task()
        task1 >> task2
        task2 >> task3
        assert dag.is_chain(
        ), "Linear chain of tasks should be identified as chain"


@pytest.mark.parametrize('create_dag,description', [
    (lambda: (dag := sky.Dag()).add(sky.Task()) or dag, "Single task"),
    (lambda: sky.Dag(), "Empty DAG"),
])
def test_is_chain_true_cases(create_dag, description):
    """Test cases where is_chain() should return True."""
    with create_dag() as dag:
        assert dag.is_chain(), f"Failed for case: {description}"


@pytest.mark.parametrize(
    'setup_dag,description',
    [
        # Branching case
        (lambda dag:
         (setattr(dag, '_tasks', [sky.Task(
         ), sky.Task(), sky.Task()]), dag.add_edge(dag.tasks[0], dag.tasks[1]),
          dag.add_edge(dag.tasks[0], dag.tasks[2])), "Branching DAG"),

        # Merging case
        (lambda dag:
         (setattr(dag, '_tasks', [sky.Task(
         ), sky.Task(), sky.Task()]), dag.add_edge(dag.tasks[0], dag.tasks[2]),
          dag.add_edge(dag.tasks[1], dag.tasks[2])), "Merging DAG"),

        # Diamond case
        (lambda dag: (setattr(dag, '_tasks', [sky.Task() for _ in range(4)]),
                      dag.add_edge(dag.tasks[0], dag.tasks[1]),
                      dag.add_edge(dag.tasks[0], dag.tasks[2]),
                      dag.add_edge(dag.tasks[1], dag.tasks[3]),
                      dag.add_edge(dag.tasks[2], dag.tasks[3])), "Diamond DAG"),
    ])
def test_is_chain_false_cases(setup_dag, description):
    """Test cases where is_chain() should return False."""
    with sky.Dag() as dag:
        setup_dag(dag)
        assert not dag.is_chain(), f"Failed for case: {description}"


@pytest.mark.regression
def test_is_chain_regression():
    """Regression test comparing new implementation with old behavior."""

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

    # Test cases where both implementations should agree
    with sky.Dag() as dag:
        task1 = sky.Task()
        task2 = sky.Task()
        task1 >> task2
        assert dag.is_chain() == old_is_chain(
            dag), "New implementation differs from old for simple chain"

    # Test case where new implementation is more correct
    with sky.Dag() as dag:
        task1, task2, task3 = sky.Task(), sky.Task(), sky.Task()
        task1 >> task3
        task2 >> task3  # Multiple parents - should not be a chain
        assert not dag.is_chain(
        ), "New implementation correctly identifies non-chain DAG"
        # Note: old implementation might incorrectly return True here


@pytest.mark.xfail(reason="Known limitation with cycles")
def test_is_chain_with_cycle():
    """Test is_chain() with cyclic graph (should raise or return False)."""
    with sky.Dag() as dag:
        task1 = sky.Task()
        task2 = sky.Task()
        task1 >> task2
        task2 >> task1  # Create cycle
        assert not dag.is_chain()
