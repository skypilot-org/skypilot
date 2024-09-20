"""Customize policy by users."""
import copy
import dataclasses
import importlib
import os
import tempfile
import typing
from typing import Any, Callable, Dict, Optional

from sky import dag as dag_lib
from sky import sky_logging
from sky import skypilot_config
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky import task as task_lib


@dataclasses.dataclass
class UserTask:
    task: 'task_lib.Task'
    skypilot_config: Dict[str, Any]


@dataclasses.dataclass
class MutatedUserTask:
    task: 'task_lib.Task'
    skypilot_config: Dict[str, Any]


class Policy:
    """User-defined policy.

    A user-defined policy is a string to a python function that can be imported
    from the same environment where SkyPilot is running.

    It can be specified in the SkyPilot config file under the key 'policy', e.g.

        policy: my_package.skypilot_policy_fn_v1

    The python function is expected to have the following signature:

        def skypilot_policy_fn_v1(user_task: UserTask) -> MutatedUserTask:
            ...
            return MutatedUserTask(task=..., skypilot_config=...)

    The function can mutate both task and skypilot_config.
    """

    def __init__(self) -> None:
        """Initialize the policy from SkyPilot config."""
        # Policy is a string to a python function within some user provided
        # module.
        self.policy: Optional[str] = skypilot_config.get_nested(('policy',),
                                                                None)
        self.policy_fn: Optional[Callable[[UserTask], MutatedUserTask]] = None
        if self.policy is not None:
            try:
                module_path, func_name = self.policy.rsplit('.', 1)
                module = importlib.import_module(module_path)
            except ImportError as e:
                with ux_utils.print_exception_no_traceback():
                    raise ImportError(
                        f'Failed to import policy module: {module_path}. '
                        'Please check if the module is in your Python '
                        'environment.') from e
            try:
                self.policy_fn = getattr(module, func_name)
            except AttributeError as e:
                with ux_utils.print_exception_no_traceback():
                    raise AttributeError(
                        f'Failed to get policy function: {func_name} from '
                        f'module: {module_path}. Please check with your policy '
                        f'admin if the function {func_name!r} is in the '
                        'module.') from e

    def apply(self, dag: 'dag_lib.Dag') -> 'dag_lib.Dag':
        """Apply user-defined policy to a DAG.

        It mutates a Dag by applying user-defined policy and also update the
        global SkyPilot config if there is any changes made by the policy.

        Args:
            dag: The dag to be mutated by the policy.

        Returns:
            The mutated dag.
        """
        if self.policy_fn is None:
            return dag
        logger.info(f'Applying policy: {self.policy}')
        original_config = skypilot_config.to_dict()
        config = copy.deepcopy(original_config)
        mutated_dag = dag_lib.Dag()
        mutated_dag.name = dag.name

        mutated_config = None
        for task in dag.tasks:
            user_task = UserTask(task, config)
            mutated_user_task = self.policy_fn(user_task)
            if mutated_config is None:
                mutated_config = mutated_user_task.skypilot_config
            else:
                if mutated_config != mutated_user_task.skypilot_config:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            'All tasks must have the same skypilot '
                            'config after applying the policy. Please'
                            'check with your policy admin for details.')
            mutated_dag.add(mutated_user_task.task)

        # Update the new_dag's graph with the old dag's graph
        for u, v in dag.graph.edges:
            u_idx = dag.tasks.index(u)
            v_idx = dag.tasks.index(v)
            mutated_dag.graph.add_edge(mutated_dag.tasks[u_idx],
                                       mutated_dag.tasks[v_idx])

        if original_config != mutated_config:
            with tempfile.NamedTemporaryFile(
                    delete=False,
                    mode='w',
                    prefix='policy-mutated-skypilot-config-',
                    suffix='.yaml') as temp_file:
                common_utils.dump_yaml(temp_file.name, mutated_config)
            os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = temp_file.name
            logger.debug(f'Updated SkyPilot config: {temp_file.name}')
            # TODO(zhwu): This is not a clean way to update the SkyPilot config,
            # because we are resetting the global context for a single DAG,
            # which is conceptually weird.
            importlib.reload(skypilot_config)

        return mutated_dag

    def apply_to_task(self, task: 'task_lib.Task') -> 'task_lib.Task':
        """Apply user-defined policy to a task.

        It mutates a task by applying user-defined policy and also update the
        global SkyPilot config if there is any changes made by the policy.

        Args:
            task: The task to be mutated by the policy.

        Returns:
            The mutated task.
        """

        dag = dag_lib.Dag()
        dag.add(task)
        dag = self.apply(dag)
        return dag.tasks[0]
