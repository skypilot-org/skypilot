"""DAGs: user applications to be run."""
import pprint
import threading
import typing
from typing import List, Optional, Dict

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
        self.dependencies: Dict['task.Task', List['task.Task']] = {}

    def add(self, task: 'task.Task') -> None:
        self.graph.add_node(task)
        self.tasks.append(task)

    def remove(self, task: 'task.Task') -> None:
        dependant = [
            task in self.graph.predecessors(node)
            for node in self.graph.nodes
        ]

        dependant_names = ", ".join([
            dep.name for dep in self.dependencies[task]
            if dep.name is not None
        ]) # TODO(andy): if the name is None, how to display?
        if any(dependant):
            raise ValueError(f'Task {task.name} is still being depended on by '
                             f'tasks {dependant_names!r}. Try to remove the '
                             'dependencies first.')

        self.dependencies.pop(task, None)

        self.tasks.remove(task)
        self.graph.remove_node(task)

    def add_edge(self, op1: 'task.Task', op2: 'task.Task') -> None:
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes

        self.dependencies[op2] += [op1]
        self.graph.add_edge(op1, op2)

    def add_dependency(self, dependant: 'task.Task',
                    dependency: 'task.Task') -> None:
        self.add_edge(dependency, dependant)

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
        # NOTE: this method assumes that the graph has no cycle.
        nodes = list(self.graph.nodes)
        out_degrees = [self.graph.out_degree(node) for node in nodes]

        return (len(nodes) <= 1 or
                (all(degree <= 1 for degree in out_degrees) and
                sum(degree == 0 for degree in out_degrees) == 1))

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
