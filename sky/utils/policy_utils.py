"""Customize policy by users."""
import copy
import importlib
import os
import tempfile
import typing
from typing import Optional, Union

from sky import dag as dag_lib
from sky import sky_logging
from sky import task as task_lib
from sky import skypilot_config
from sky.utils import common_utils
from sky.utils import ux_utils
from sky import policy as policy_lib

logger = sky_logging.init_logger(__name__)



def _get_policy_cls() -> Optional[policy_lib.AdminPolicy]:
    """Get admin-defined policy."""
    policy = skypilot_config.get_nested(('admin_policy',), None)
    if policy is None:
        return None
    try:
        module_path, class_name = policy.rsplit('.', 1)
        module = importlib.import_module(module_path)
    except ImportError as e:
        with ux_utils.print_exception_no_traceback():
            raise ImportError(
                f'Failed to import policy module: {policy}. '
                'Please check if the module is installed in your Python '
                'environment.') from e

    try:
        policy_cls = getattr(module, class_name)
    except AttributeError as e:
        with ux_utils.print_exception_no_traceback():
            raise AttributeError(
                f'Could not find {class_name} class in module {module_path}. '
                'Please check with your policy admin for details.') from e

    # Check if the module implements the AdminPolicy interface.
    if not issubclass(policy_cls, policy_lib.AdminPolicy):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Policy module {policy} does not implement the AdminPolicy '
                'interface. Please check with your policy admin for details.')
    return policy_cls

@typing.overload
def apply(dag: 'dag_lib.Dag') -> 'dag_lib.Dag':
    ...

@typing.overload
def apply(dag: 'task_lib.Task') -> 'task_lib.Task':
    ...

def apply(entrypoint: Union['dag_lib.Dag', 'task_lib.Task']) -> Union['dag_lib.Dag', 'task_lib.Task']:
    """Apply user-defined policy to a DAG or a task.

    It mutates a Dag by applying user-defined policy and also update the
    global SkyPilot config if there is any changes made by the policy.

    Args:
        dag: The dag to be mutated by the policy.

    Returns:
        The mutated dag or task.
    """
    if isinstance(entrypoint, task_lib.Task):
        return _apply_to_task(entrypoint)

    dag = entrypoint

    policy_cls = _get_policy_cls()
    if policy_cls is None:
        return dag
    logger.info(f'Applying policy: {policy_cls}')
    import traceback
    logger.debug(f'Stack trace: {traceback.format_stack()}')
    original_config = skypilot_config.to_dict()
    config = copy.deepcopy(original_config)
    mutated_dag = dag_lib.Dag()
    mutated_dag.name = dag.name

    mutated_config = None
    for task in dag.tasks:
        user_request = policy_lib.UserRequest(task, config)
        mutated_user_request = policy_cls.validate_and_mutate(user_request)
        if mutated_config is None:
            mutated_config = mutated_user_request.skypilot_config
        else:
            if mutated_config != mutated_user_request.skypilot_config:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'All tasks must have the same skypilot '
                        'config after applying the policy. Please'
                        'check with your policy admin for details.')
        mutated_dag.add(mutated_user_request.task)

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

    logger.debug(f'Mutated user request: {mutated_user_request}')
    return mutated_dag

def _apply_to_task(task: 'task_lib.Task') -> 'task_lib.Task':
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
    dag = apply(dag)
    return dag.tasks[0]
