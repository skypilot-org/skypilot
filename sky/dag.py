"""DAGs: user applications to be run."""
import pprint
import threading
import typing
from typing import List, Optional

if typing.TYPE_CHECKING:
    from sky import task


class Dag:
    """Dag: a user application, represented as a DAG of Tasks.

    Examples:
        >>> import sky
        >>> with sky.Dag() as dag:
        >>>     task = sky.Task(...)
    """

    def __init__(self) -> None:
        self.tasks: List['task.Task'] = []
        import networkx as nx  # pylint: disable=import-outside-toplevel

        self.graph = nx.DiGraph()
        self.name: Optional[str] = None
        self.policy_applied: bool = False

    def add(self, task: 'task.Task') -> None:
        self.graph.add_node(task)
        self.tasks.append(task)

    def remove(self, task: 'task.Task') -> None:
        self.tasks.remove(task)
        self.graph.remove_node(task)

    def add_edge(self, op1: 'task.Task', op2: 'task.Task') -> None:
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __len__(self) -> int:
        return len(self.tasks)

    def __enter__(self) -> 'Dag':
        push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        pop_dag()

    def __repr__(self) -> str:
        pformat = pprint.pformat(self.tasks)
        return f'DAG:\n{pformat}'

    def get_graph(self):
        return self.graph

    def is_chain(self) -> bool:
        """Check if the DAG is a linear chain of tasks."""

        nodes = list(self.graph.nodes)

        if len(nodes) == 0:
            return True

        in_degrees = [self.graph.in_degree(node) for node in nodes]
        out_degrees = [self.graph.out_degree(node) for node in nodes]

        # Check out-degrees: all <= 1 and exactly one node has out_degree == 0
        out_degree_condition = (all(degree <= 1 for degree in out_degrees) and
                                sum(degree == 0 for degree in out_degrees) == 1)

        # Check in-degrees: all <= 1 and exactly one node has in_degree == 0
        in_degree_condition = (all(degree <= 1 for degree in in_degrees) and
                               sum(degree == 0 for degree in in_degrees) == 1)

        return out_degree_condition and in_degree_condition

    def validate(self, workdir_only: bool = False):
        for task in self.tasks:
            task.validate(workdir_only=workdir_only)


class _DagContext(threading.local):
    """A thread-local stack of Dags."""
    _current_dag: Optional[Dag] = None
    _previous_dags: List[Dag] = []

    def push_dag(self, dag: Dag):
        if self._current_dag is not None:
            self._previous_dags.append(self._current_dag)
        self._current_dag = dag

    def pop_dag(self) -> Optional[Dag]:
        old_dag = self._current_dag
        if self._previous_dags:
            self._current_dag = self._previous_dags.pop()
        else:
            self._current_dag = None
        return old_dag

    def get_current_dag(self) -> Optional[Dag]:
        return self._current_dag


_dag_context = _DagContext()
# Exposed via `sky.dag.*`.
push_dag = _dag_context.push_dag
pop_dag = _dag_context.pop_dag
get_current_dag = _dag_context.get_current_dag
