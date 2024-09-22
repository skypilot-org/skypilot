"""Customize policy by users."""
import copy
import importlib
import os
import tempfile
import typing
from typing import Literal, Optional, Tuple, Union

import colorama

from sky import dag as dag_lib
from sky import exceptions
from sky import policy as policy_lib
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def _get_policy_cls(policy: Optional[str]) -> Optional[policy_lib.AdminPolicy]:
    """Gets admin-defined policy."""
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
def apply(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    apply_skypilot_config: Literal[True] = True,
    operation_args: Optional[policy_lib.OperationArgs] = None,
) -> 'dag_lib.Dag':
    ...


@typing.overload
def apply(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    apply_skypilot_config: Literal[False],
    operation_args: Optional[policy_lib.OperationArgs] = None,
) -> Tuple['dag_lib.Dag', skypilot_config.NestedConfig]:
    ...


def apply(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    apply_skypilot_config: bool = True,
    operation_args: Optional[policy_lib.OperationArgs] = None,
) -> Union['dag_lib.Dag', Tuple['dag_lib.Dag', skypilot_config.NestedConfig]]:
    """Applies user-defined policy to a DAG or a task.

    It mutates a Dag by applying user-defined policy and also updates the
    global SkyPilot config if there is any changes made by the policy.

    Args:
        dag: The dag to be mutated by the policy.
        apply_skypilot_config: Whether to apply the skypilot config changes to
            the global skypilot config.

    Returns:
        The mutated dag or task.
        Or, a tuple of the mutated dag and path to the mutated skypilot
        config, if apply_skypilot_config is set to False.
    """
    if isinstance(entrypoint, task_lib.Task):
        dag = dag_lib.Dag()
        dag.add(entrypoint)
    else:
        dag = entrypoint

    policy = skypilot_config.get_nested(('admin_policy',), None)
    policy_cls = _get_policy_cls(policy)
    if policy_cls is None:
        if apply_skypilot_config:
            return dag
        else:
            return dag, skypilot_config.to_dict()

    logger.info(f'Applying policy: {policy}')
    original_config = skypilot_config.to_dict()
    config = copy.deepcopy(original_config)
    mutated_dag = dag_lib.Dag()
    mutated_dag.name = dag.name

    mutated_config = None
    for task in dag.tasks:
        user_request = policy_lib.UserRequest(task, config, operation_args)
        try:
            mutated_user_request = policy_cls.validate_and_mutate(user_request)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.UserRequestRejectedByPolicy(
                    f'{colorama.Fore.RED}User request rejected by policy '
                    f'{policy!r}{colorama.Fore.RESET}: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e
        if mutated_config is None:
            mutated_config = mutated_user_request.skypilot_config
        else:
            if mutated_config != mutated_user_request.skypilot_config:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.UserRequestRejectedByPolicy(
                        'All tasks must have the same skypilot '
                        'config after applying the policy. Please'
                        'check with your policy admin for details.')
        mutated_dag.add(mutated_user_request.task)
    assert mutated_config is not None, dag

    # Update the new_dag's graph with the old dag's graph
    for u, v in dag.graph.edges:
        u_idx = dag.tasks.index(u)
        v_idx = dag.tasks.index(v)
        mutated_dag.graph.add_edge(mutated_dag.tasks[u_idx],
                                   mutated_dag.tasks[v_idx])

    if apply_skypilot_config and original_config != mutated_config:
        with tempfile.NamedTemporaryFile(
                delete=False,
                mode='w',
                prefix='policy-mutated-skypilot-config-',
                suffix='.yaml') as temp_file:

            common_utils.dump_yaml(temp_file.name, dict(**mutated_config))
            os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = temp_file.name
            logger.debug(f'Updated SkyPilot config: {temp_file.name}')
            # TODO(zhwu): This is not a clean way to update the SkyPilot config,
            # because we are resetting the global context for a single DAG,
            # which is conceptually weird.
            importlib.reload(skypilot_config)

    logger.debug(f'Mutated user request: {mutated_user_request}')
    if apply_skypilot_config:
        return mutated_dag
    else:
        return mutated_dag, mutated_config
