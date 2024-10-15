"""DAGs: user applications to be run."""
import threading
import typing
from typing import cast, Dict, List, Optional, Set, Union

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
        self.dependencies: Dict['task.Task', Set['task.Task']] = {}
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
            task.name = common_utils.get_unique_task_name(task)
        if task.name in self._task_name_lookup:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Task {task.name!r} already exists in the DAG.'
                    f' Or the task name is already used by another task.')
        self.graph.add_node(task)
        self._task_name_lookup[task.name] = task

    def remove(self, task: Union['task.Task', str]) -> None:
        """Remove a task from the DAG.

        Args:
            task: The Task object or name of the task to remove.

        Raises:
            ValueError: If the task is still being depended on by other tasks.
        """
        task = self._get_task(task)

        dependents = list(self.graph.successors(task))
        for dependent in dependents:
            self.remove_dependency(dependent, task)

        # TODO(andy): Stuck by optimizer's wrong way to remove dummy sources
        # and sink nodes.
        # if dependents:
        #     dependent_names = ', '.join([dep.name for dep in dependents])
        #     with ux_utils.print_exception_no_traceback():
        #         raise ValueError(f'Task {task.name} is still being depended '
        #                          f'by tasks {dependent_names!r}. Try to '
        #                          'remove the dependencies first.')

        self.dependencies.pop(task, None)
        self.graph.remove_node(task)
        assert task.name is not None
        self._task_name_lookup.pop(task.name, None)

    def add_dependency(self, dependent: TaskOrName,
                       dependency: TaskOrName) -> None:
        """Add a single dependency for a task.

        This method adds a new dependency without removing existing ones.

        Args:
            dependent: The task that depends on another.
            dependency: The task that the dependent task depends on.

        Raises:
            ValueError: If a task tries to depend on itself or if the
            dependency task is not in the DAG.
        """
        dependent = self._get_task(dependent)
        dependency = self._get_task(dependency)

        if dependent.name == dependency.name:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Task {dependent.name} cannot depend on itself.')
        assert dependency.name is not None
        if dependency.name not in self._task_name_lookup:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Dependency task {dependency.name} is not in the DAG.')

        self.graph.add_edge(dependency, dependent)
        if dependent not in self.dependencies:
            self.dependencies[dependent] = set()
        self.dependencies[dependent].add(dependency)

    # Backward compatibility for old DAG API.
    # TODO(andy): Remove this after 2 minor releases, which is 0.6.3.
    # Be careful the parameter order is different from add_dependency.
    def add_edge(self, op1: 'task.Task', op2: 'task.Task') -> None:
        return self.add_dependency(op2, op1)

    def set_dependencies(
            self, dependent: TaskOrName,
            dependencies: Union[List[TaskOrName], TaskOrName]) -> None:
        """Set dependencies for a task, replacing any existing dependencies.

        Args:
            dependent: The task to set dependencies for.
            dependencies: The task(s) that the dependent task depends on.
        """
        dependent = self._get_task(dependent)
        self.remove_all_dependencies(dependent)
        if not isinstance(dependencies, list):
            dependencies = [dependencies]
        for dependency in dependencies:
            self.add_dependency(dependent, dependency)

    def remove_dependency(self, dependent: TaskOrName,
                          dependency: TaskOrName) -> None:
        """Remove a specific dependency for a task.

        Args:
            dependent: The task to remove a dependency from.
            dependency: The dependency to remove.
        """
        dependent = self._get_task(dependent)
        dependency = self._get_task(dependency)

        if dependent in self.dependencies and dependency in self.dependencies[
                dependent]:
            self.dependencies[dependent].remove(dependency)
            self.graph.remove_edge(dependency, dependent)

    def remove_all_dependencies(self, task: TaskOrName) -> None:
        """Remove all dependencies for a given task.

        Args:
            task: The task to remove all dependencies from.
        """
        task = self._get_task(task)
        if task in self.dependencies:
            for dependency in list(self.dependencies[task]):
                self.graph.remove_edge(dependency, task)
            self.dependencies[task].clear()

    def get_dependencies(self, task: TaskOrName) -> Set['task.Task']:
        """Get all dependencies for a given task.

        Args:
            task: The task to get dependencies for.

        Returns:
            A set of tasks that the given task depends on.
        """
        task = self._get_task(task)
        return self.dependencies.get(task, set())

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
            deps = self.get_dependencies(task)
            dep_names = ','.join(
                cast(str, dep.name) for dep in deps) if deps else '-'
            task_info.append(f'{task.name}({dep_names})')

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
