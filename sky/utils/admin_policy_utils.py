"""Admin policy utils."""
import contextlib
import copy
import importlib
import typing
from typing import Iterator, Optional, Tuple, Union
from urllib import parse as urlparse

import colorama

from sky import admin_policy
from sky import dag as dag_lib
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.server.requests import request_names
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky import models


def _is_url(policy_string: str) -> bool:
    """Check if the policy string is a URL."""
    try:
        parsed = urlparse.urlparse(policy_string)
        return parsed.scheme in ('http', 'https')
    except Exception:  # pylint: disable=broad-except
        return False


def _get_policy_impl(
        policy_location: Optional[str]
) -> Optional[admin_policy.PolicyInterface]:
    """Gets admin-defined policy."""
    if policy_location is None:
        return None

    if _is_url(policy_location):
        # Use the built-in URL policy class when an URL is specified.
        return admin_policy.RestfulAdminPolicy(policy_location)

    # Handle module path format
    try:
        module_path, class_name = policy_location.rsplit('.', 1)
        module = importlib.import_module(module_path)
    except ImportError as e:
        with ux_utils.print_exception_no_traceback():
            raise ImportError(
                f'Failed to import policy module: {policy_location}. '
                'Please check if the module is installed in your Python '
                'environment.') from e

    try:
        policy_cls = getattr(module, class_name)
    except AttributeError as e:
        with ux_utils.print_exception_no_traceback():
            raise AttributeError(
                f'Could not find {class_name} class in module {module_path}. '
                'Please check with your policy admin for details.') from e

    # Currently we only allow users to define subclass of AdminPolicy
    # instead of inheriting from PolicyInterface or PolicyTemplate.
    if not issubclass(policy_cls, admin_policy.AdminPolicy):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Policy class {policy_cls!r} does not implement the '
                'AdminPolicy interface. Please check with your policy admin '
                'for details.')
    return policy_cls()


@contextlib.contextmanager
def apply_and_use_config_in_current_request(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    request_name: request_names.AdminPolicyRequestName,
    request_options: Optional[admin_policy.RequestOptions] = None,
    at_client_side: bool = False,
) -> Iterator['dag_lib.Dag']:
    """Applies an admin policy and override SkyPilot config for current request

    This is a helper function of `apply()` that applies an admin policy and
    overrides the SkyPilot config for the current request as a context manager.
    The original SkyPilot config will be restored when the context manager is
    exited.

    Refer to `apply()` for more details.
    """
    original_config = skypilot_config.to_dict()
    dag, mutated_config = apply(entrypoint, request_name, request_options,
                                at_client_side)
    if mutated_config != original_config:
        with skypilot_config.replace_skypilot_config(mutated_config):
            yield dag
    else:
        yield dag


def apply(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    request_name: request_names.AdminPolicyRequestName,
    request_options: Optional[admin_policy.RequestOptions] = None,
    at_client_side: bool = False,
) -> Tuple['dag_lib.Dag', config_utils.Config]:
    """Applies an admin policy (if registered) to a DAG or a task.

    It mutates a Dag by applying any registered admin policy and also
    potentially updates (controlled by `use_mutated_config_in_current_request`)
    the global SkyPilot config if there is any changes made by the policy.

    Args:
        dag: The dag to be mutated by the policy.
        use_mutated_config_in_current_request: Whether to use the mutated
            config in the current request.
        request_options: Additional options user passed for the current request.

    Returns:
        - The new copy of dag after applying the policy
        - The new copy of skypilot config after applying the policy.
    """
    if isinstance(entrypoint, task_lib.Task):
        dag = dag_lib.Dag()
        dag.add(entrypoint)
    else:
        dag = entrypoint

    policy_location = skypilot_config.get_nested(('admin_policy',), None)
    policy = _get_policy_impl(policy_location)
    if policy is None:
        return dag, skypilot_config.to_dict()

    user = None
    if at_client_side:
        logger.info(f'Applying client admin policy: {policy}')
    else:
        # When being called by the server, the middleware has set the
        # current user and this information is available at this point.
        user = common_utils.get_current_user()
        logger.info(f'Applying server admin policy: {policy}')
    config = copy.deepcopy(skypilot_config.to_dict())
    mutated_dag = dag_lib.Dag()
    mutated_dag.name = dag.name
    # Preserve DAG execution properties if set
    if dag.is_job_group():
        assert dag.execution is not None
        mutated_dag.set_execution(dag.execution)

    mutated_config = None
    for task in dag.tasks:
        user_request = admin_policy.UserRequest(task, config, request_name,
                                                request_options, at_client_side,
                                                user)
        try:
            mutated_user_request = policy.apply(user_request)
        # Avoid duplicate exception wrapping.
        except exceptions.UserRequestRejectedByPolicy as e:
            with ux_utils.print_exception_no_traceback():
                raise e
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.UserRequestRejectedByPolicy(
                    f'{colorama.Fore.RED}User request rejected by policy '
                    f'{policy!r}{colorama.Fore.RESET}: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from None
        if mutated_config is None:
            mutated_config = mutated_user_request.skypilot_config
        else:
            if mutated_config != mutated_user_request.skypilot_config:
                # In the case of a pipeline of tasks, the mutated config
                # generated should remain the same for all tasks for now for
                # simplicity.
                # TODO(zhwu): We should support per-task mutated config or
                # allowing overriding required global config in task YAML.
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.UserRequestRejectedByPolicy(
                        'All tasks must have the same SkyPilot config after '
                        'applying the policy. Please check with your policy '
                        'admin for details.')
        mutated_dag.add(mutated_user_request.task)
    assert mutated_config is not None, dag

    # Update the new_dag's graph with the old dag's graph
    for u, v in dag.graph.edges:
        u_idx = dag.tasks.index(u)
        v_idx = dag.tasks.index(v)
        mutated_dag.graph.add_edge(mutated_dag.tasks[u_idx],
                                   mutated_dag.tasks[v_idx])

    logger.debug(f'Mutated user request: {mutated_user_request}')
    mutated_dag.policy_applied = True
    return mutated_dag, mutated_config
