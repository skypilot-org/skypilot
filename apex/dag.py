"""DAGs: user applications to be run."""
import pprint
import threading
from typing import List, Optional


class Dag:
    """Dag: a user application, represented as a DAG of Tasks.

    Examples:
        >>> import sky
        >>> with sky.Dag() as dag:
        >>>     task = sky.Task(...)
    """

    def __init__(self):
        self.tasks = []
        import networkx as nx  # pylint: disable=import-outside-toplevel

        self.graph = nx.DiGraph()
        self.name = None

    def add(self, task):
        self.graph.add_node(task)
        self.tasks.append(task)

    def remove(self, task):
        self.tasks.remove(task)
        self.graph.remove_node(task)

    def add_edge(self, op1, op2):
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __len__(self):
        return len(self.tasks)

    def __enter__(self):
        push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pop_dag()

    def __repr__(self):
        pformat = pprint.pformat(self.tasks)
        return f'DAG:\n{pformat}'

    def get_graph(self):
        return self.graph

    def is_chain(self) -> bool:
        # NOTE: this method assumes that the graph has no cycle.
        is_chain = True
        visited_zero_out_degree = False
        for node in self.graph.nodes:
            out_degree = self.graph.out_degree(node)
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


class _DagContext(threading.local):
    """A thread-local stack of Dags."""
    _current_dag = None
    _previous_dags: List[Dag] = []

    def push_dag(self, dag):
        if self._current_dag is not None:
            self._previous_dags.append(self._current_dag)
        self._current_dag = dag

    def pop_dag(self):
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
