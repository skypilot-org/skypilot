import networkx as nx
import pprint


class DagContext(object):
    _current_dag = None
    _previous_dags = []

    @classmethod
    def push_dag(cls, dag):
        if cls._current_dag:
            cls._previous_dags.append(cls._current_dag)
        cls._current_dag = dag

    @classmethod
    def pop_dag(cls):
        old_dag = cls._current_dag
        if cls._previous_dags:
            cls._current_dag = cls._previous_dags.pop()
        else:
            cls._current_dag = None
        return old_dag

    @classmethod
    def get_current_dag(cls):
        return cls._current_dag


class Dag(object):
    """FIXME: assume a chain DAG for now."""

    _PREVIOUS_DAGS = []
    _CURRENT_DAG = None

    def __init__(self):
        self.tasks = []
        self.graph = nx.DiGraph()

    def add(self, task):
        self.graph.add_node(task)
        self.tasks.append(task)

    def add_edge(self, op1, op2):
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __enter__(self):
        DagContext.push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        DagContext.pop_dag()

    def __repr__(self):
        pformat = pprint.pformat(self.tasks)
        return 'DAG:\n{}'.format(pformat)
        # return '<DAG=[{}]>'.format(','.join(map(str, self.tasks)))

    def get_graph(self):
        return self.graph
