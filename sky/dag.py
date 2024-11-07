"""DAGs: user applications to be run."""
import tempfile
import threading
import typing
from typing import Dict, List, Optional, Set, Union

import networkx as nx

from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import task

TaskOrName = Union['task.Task', str]


class Dag:
    """Dag: a user application, represented as a DAG of Tasks.

    This class allows users to define and manage directed acyclic graphs
    (DAGs) of tasks, representing complex workflows.

    Examples:
        >>> import sky
        >>> with sky.Dag(name='my_pipeline') as dag:
        >>>     task1 = sky.Task(name='task1', ...)
        >>>     task2 = sky.Task(name='task2', ...)
        >>>     task1 >> task2
    """

    def __init__(self, name: Optional[str] = None) -> None:
        """Initialize a new DAG.

        Args:
            name: Optional name for the DAG.
        """
        self.name = name
        self._task_name_lookup: Dict[str, 'task.Task'] = {}
        self.graph = nx.DiGraph()

    @property
    def tasks(self) -> List['task.Task']:
        """Return a list of all tasks in the DAG."""
        return list(self._task_name_lookup.values())

    def _get_task(self, task_or_name: TaskOrName) -> 'task.Task':
        """Get a task object from a task or its name.

        Args:
            task_or_name: Either a Task object or the name of a task.

        Returns:
            The Task object.

        Raises:
            ValueError: If the task name is not found in the DAG.
        """
        if not isinstance(task_or_name, str):
            return task_or_name
        name = task_or_name
        if name not in self._task_name_lookup:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Task {name} not found in DAG')
        return self._task_name_lookup[name]

    def add(self, task: 'task.Task') -> None:
        """Add a task to the DAG.

        Args:
            task: The Task object to add.

        Raises:
            ValueError: If the task already exists in the DAG or if its name
            is already used.
        """
        if task.name is None:
            task.name = common_utils.get_unique_task_name()
        if task.name in self._task_name_lookup:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Task {task.name!r} already exists in the DAG, '
                    f'or the task name is already used by another task.')
        self.graph.add_node(task)
        self._task_name_lookup[task.name] = task

    def remove(self, task: TaskOrName) -> None:
        """Remove a task from the DAG.

        Args:
            task: The Task object or name of the task to remove.

        Raises:
            ValueError: If the task is still being used as a upstream task by
            other tasks.
        """
        task = self._get_task(task)

        downstreams = self.get_downstream(task)
        # TODO(andy): Stuck by optimizer's wrong way to remove dummy sources
        # and sink nodes.
        # if downstreams:
        #     downstream_names = ', '.join(
        #         cast(str, downstream.name) for downstream in downstreams)
        #     with ux_utils.print_exception_no_traceback():
        #         raise ValueError(f'Task {task.name} is still being used as a '
        #                          f'downstream task by {downstream_names!r}. '
        #                          'Try to remove the downstream tasks first.')
        # Here's a workaround, proactively remove all downstream edges.
        for downstream in downstreams:
            self.remove_edge(task, downstream)

        self.graph.remove_node(task)
        assert task.name is not None
        self._task_name_lookup.pop(task.name, None)

    def add_edge(self, source: TaskOrName, target: TaskOrName) -> None:
        """Add an edge from source task to target task.

        Args:
            source: The upstream task.
            target: The downstream task to be added.

        Raises:
            ValueError: If a task is set as its own downstream task or if the
            downstream task is not in the DAG.
        """
        source = self._get_task(source)
        target = self._get_task(target)

        if source.name == target.name:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Task {source.name} should not be its own '
                                 'downstream task.')

        self.graph.add_edge(source, target)

    def add_downstream(self, source: TaskOrName, target: TaskOrName) -> None:
        """Add downstream tasks for a source task.

        Args:
            source: The upstream task.
            target: The downstream task to be added.
        """
        return self.add_edge(source, target)

    def set_downstream(self, source: TaskOrName,
                       targets: Union[List[TaskOrName], TaskOrName]) -> None:
        """Set downstream tasks for a source task.

        This replaces any existing downstream tasks for the given source.

        Args:
            source: The upstream task.
            targets: The downstream task(s) to be added.
        """
        source = self._get_task(source)
        if not isinstance(targets, list):
            targets = [targets]
        self.remove_all_downstream(source)
        for target in targets:
            self.add_edge(source, target)

    def remove_edge(self, source: TaskOrName, target: TaskOrName) -> None:
        """Remove an edge between two tasks.

        Args:
            source: The upstream task.
            target: The downstream task to remove the edge to.
        """
        source = self._get_task(source)
        target = self._get_task(target)
        try:
            self.graph.remove_edge(source, target)
        except nx.NetworkXError:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Edge {source.name} -> {target.name} not found') from None

    def remove_all_downstream(self, task: TaskOrName) -> None:
        """Remove all downstream tasks for a given task.

        Args:
            task: The task to remove all downstream tasks from.
        """
        task = self._get_task(task)
        for target in self.graph.successors(task):
            self.graph.remove_edge(task, target)

    def get_downstream(self, task: TaskOrName) -> Set['task.Task']:
        """Get all downstream tasks for a given task.

        Args:
            task: The task to get downstream tasks for.

        Returns:
            A set of downstream tasks.
        """
        task = self._get_task(task)
        return set(self.graph.successors(task))

    def __len__(self) -> int:
        """Return the number of tasks in the DAG."""
        return len(self._task_name_lookup)

    def __enter__(self) -> 'Dag':
        """Enter the runtime context related to this object."""
        push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Exit the runtime context related to this object."""
        pop_dag()

    def __repr__(self) -> str:
        """Return a string representation of the DAG."""
        task_info = []
        for task in self.tasks:
            downstream = self.get_downstream(task)
            downstream_names = ','.join(
                typing.cast(str, dep.name)
                for dep in downstream) if downstream else '-'
            task_info.append(f'{task.name}'
                             f'({downstream_names})')

        tasks_str = ' '.join(task_info)
        return f'DAG({self.name}: {tasks_str})'

    def get_graph(self):
        """Return the networkx graph representing the DAG."""
        return self.graph

    def is_chain(self) -> bool:
        """Check if the DAG is a linear chain of tasks.

        Returns:
            True if the DAG is a linear chain, False otherwise.
        """
        nodes = list(self.graph.nodes)
        out_degrees = [self.graph.out_degree(node) for node in nodes]

        return (len(nodes) <= 1 or
                (all(degree <= 1 for degree in out_degrees) and
                 sum(degree == 0 for degree in out_degrees) == 1))

    def is_connected_dag(self) -> bool:
        """Check if the graph is a connected directed acyclic graph (DAG).

        Returns:
            True if the graph is a connected DAG (weakly connected,
            directed and acyclic), False otherwise.
        """

        # A graph is weakly connected if replacing all directed edges with
        # undirected edges produces a connected graph, i.e., any two nodes
        # can reach each other ignoring edge directions.
        if not nx.is_weakly_connected(self.graph):
            return False

        return nx.is_directed_acyclic_graph(self.graph)

    def plot(self, to_file: Optional[str] = None) -> Optional[str]:
        """Visualize the DAG structure and save or display it as an image.

        Args:
            to_file: Optional; the file path to save the DAG visualization.
                    If not provided, a temporary file will be created.

        Returns:
            The file path to the saved image, or None if displayed in Jupyter.
        """

        # Import matplotlib at runtime to keep core installation lightweight.
        # Raises ImportError if not installed, prompting user to install
        # manually.
        try:
            # pylint: disable=import-outside-toplevel
            import matplotlib.pyplot as plt
        except ImportError:
            with ux_utils.print_exception_no_traceback():
                raise ImportError(
                    'matplotlib is not required for DAG visualization. '
                    'Please install it using `pip install matplotlib`.'
                ) from None

        pos = nx.spring_layout(self.graph, k=0.7, seed=42)
        _, ax = plt.subplots(figsize=(10, 8))

        nx.draw(self.graph,
                pos,
                ax=ax,
                node_size=1000,
                node_color='skyblue',
                font_size=8,
                font_weight='bold',
                arrows=True)

        labels: Dict['task.Task',
                     str] = {node: str(node) for node in self.graph.nodes()}

        nx.draw_networkx_labels(self.graph,
                                pos,
                                labels,
                                font_size=7,
                                ax=ax,
                                verticalalignment='center',
                                horizontalalignment='center',
                                bbox=dict(facecolor='white',
                                          edgecolor='gray',
                                          boxstyle='round,pad=0.3'))

        ax.margins(0.2)
        plt.subplots_adjust(left=0.15, right=0.85, top=0.85, bottom=0.15)

        if to_file is None:
            tmp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            to_file = tmp_file.name
            tmp_file.close()

        plt.savefig(to_file, bbox_inches='tight')
        plt.close()

        try:
            # Try to display the image in Jupyter Notebook
            # pylint: disable=import-outside-toplevel
            from IPython.display import display
            from IPython.display import Image
            display(Image(filename=to_file))
            return None
        except ImportError:
            pass

        return to_file


class _DagContext(threading.local):
    """A thread-local stack of Dags."""
    _current_dag: Optional[Dag] = None
    _previous_dags: List[Dag] = []

    def push_dag(self, dag: Dag):
        """Push a DAG onto the stack."""
        if self._current_dag is not None:
            self._previous_dags.append(self._current_dag)
        self._current_dag = dag

    def pop_dag(self) -> Optional[Dag]:
        """Pop the current DAG from the stack."""
        old_dag = self._current_dag
        if self._previous_dags:
            self._current_dag = self._previous_dags.pop()
        else:
            self._current_dag = None
        return old_dag

    def get_current_dag(self) -> Optional[Dag]:
        """Get the current DAG."""
        return self._current_dag


_dag_context = _DagContext()
# Exposed via `sky.dag.*`.
push_dag = _dag_context.push_dag
pop_dag = _dag_context.pop_dag
get_current_dag = _dag_context.get_current_dag
