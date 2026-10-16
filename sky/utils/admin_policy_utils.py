"""Admin policy utils."""
import contextlib
import copy
import dataclasses
import importlib
import typing
from typing import Iterator, Optional, Tuple, Union
from urllib import parse as urlparse

import colorama

from sky import admin_policy
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.server import versions
from sky.server.requests import request_names
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import ux_utils
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky import models

# Requests a controller issues for resources it already placed; they keep
# their workspace. The names are trustworthy: server.py rejects the
# controller-only LaunchBody fields from non-loopback callers, and the
# *_controller requests are in-process only.
_CONTROLLER_REQUEST_NAMES = frozenset({
    request_names.AdminPolicyRequestName.JOBS_LAUNCH_CONTROLLER,
    request_names.AdminPolicyRequestName.SERVE_LAUNCH_CONTROLLER,
    request_names.AdminPolicyRequestName.JOBS_LAUNCH_CLUSTER,
    request_names.AdminPolicyRequestName.SERVE_LAUNCH_REPLICA,
})


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
    # Mirror the resolved-workspace injection done in `apply()` so the
    # comparison below only triggers a config replacement when the policy
    # actually mutated the config, not merely because `apply()` surfaced the
    # resolved active workspace.
    original_config = skypilot_config.resolved_config()
    dag, mutated_config = apply(entrypoint, request_name, request_options,
                                at_client_side)
    if mutated_config == original_config:
        yield dag
    elif at_client_side:
        with skypilot_config.replace_skypilot_config(mutated_config):
            yield dag
    else:
        with skypilot_config.replace_skypilot_config(mutated_config):
            with use_policy_workspace(mutated_config['active_workspace']):
                yield dag


@contextlib.contextmanager
def use_policy_workspace(workspace: str) -> Iterator[None]:
    """Runs the body in `workspace`, leaving the rest of the config as is.

    The config is installed before the context, so threads and subprocesses
    see it, and a nested local_active_workspace_ctx (a controller launch in
    'default') really switches. A no-op if the request is in `workspace`.
    """
    if workspace == skypilot_config.get_active_workspace():
        yield
        return
    config = skypilot_config.to_dict()
    config['active_workspace'] = workspace
    # Long requests run without a sky context (only coroutine requests call
    # context.initialize), so this swaps the config their pool threads read.
    # A coroutine request would need context_utils propagation for threads.
    with skypilot_config.replace_skypilot_config(config):
        # Overrides a workspace the per-user resolver set in this thread.
        with skypilot_config.local_active_workspace_ctx(workspace):
            yield


def _resolve_workspace_ref(ref: str) -> str:
    """Returns the workspace a policy's `active_workspace` value refers to.

    Raises:
        exceptions.UserRequestRejectedByPolicy: no such workspace.
    """
    if (ref != constants.SKYPILOT_DEFAULT_WORKSPACE and
            ref not in skypilot_config.get_nested(('workspaces',), {})):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.UserRequestRejectedByPolicy(
                f'Workspace {ref} set by the admin policy does not exist. '
                'Use `sky check` to see if it is defined on the API server and '
                'try again.')
    return ref


def _route_workspace(
    mutated_config: config_utils.Config,
    request_name: request_names.AdminPolicyRequestName,
    request_options: Optional[admin_policy.RequestOptions],
    user: 'models.User',
    existing_resource: Optional[Tuple[str, str]],
) -> None:
    """Sets the workspace a server-side request runs in after the policy.

    A user request follows the policy's `active_workspace`. A controller
    request, a service update, and a request on an existing resource (a
    cluster, or `existing_resource`) keep the workspace of what they act on.
    The result is always written back to `mutated_config['active_workspace']`.

    Raises:
        exceptions.UserRequestRejectedByPolicy: the policy's workspace does not
            exist, or the request would move to a workspace the user cannot
            write to.
    """
    request_ws = skypilot_config.get_active_workspace()
    policy_ref = mutated_config.get('active_workspace')
    policy_ws = _resolve_workspace_ref(policy_ref) if policy_ref else request_ws
    workspace = policy_ws
    if policy_ws != request_ws:
        cluster_name = (request_options.cluster_name
                        if request_options is not None else None)
        if existing_resource is None and cluster_name:
            cluster_ws = global_user_state.get_cluster_workspace(cluster_name)
            if cluster_ws is not None:
                existing_resource = (f'existing cluster {cluster_name!r}',
                                     cluster_ws)
        source = 'admin policy'
        reason = None
        if request_name in _CONTROLLER_REQUEST_NAMES:
            workspace = request_ws
            reason = 'it is issued by a controller'
        elif request_name == request_names.AdminPolicyRequestName.SERVE_UPDATE:
            workspace = request_ws
            reason = 'a service keeps its workspace on update'
        elif (existing_resource is not None and
              existing_resource[1] != policy_ws):
            resource, workspace = existing_resource
            source = f'workspace of {resource}'
            reason = f'{resource} is in workspace {workspace!r}'
        if reason is not None:
            logger.warning(f'The admin policy set workspace {policy_ws!r} for '
                           f'request {request_name}, but it stays in '
                           f'workspace {workspace!r}: {reason}.')
        if workspace != request_ws:
            # A policy may route a request, but cannot grant access.
            try:
                workspaces_core.check_workspace_permission(
                    user,
                    workspace,
                    action=workspace_constants.WORKSPACE_ACTION_WRITE)
            except exceptions.PermissionDeniedError:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.UserRequestRejectedByPolicy(
                        f'Cannot run this request in workspace {workspace!r} '
                        f'(source: {source}): user {user.name!r} has no write '
                        'access to it.') from None
            logger.info(f'Using workspace {workspace!r} (source: {source}).')
    mutated_config['active_workspace'] = workspace


def apply(
    entrypoint: Union['dag_lib.Dag', 'task_lib.Task'],
    request_name: request_names.AdminPolicyRequestName,
    request_options: Optional[admin_policy.RequestOptions] = None,
    at_client_side: bool = False,
    existing_resource: Optional[Tuple[str, str]] = None,
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
        existing_resource: (description, workspace) of an existing resource
            the request acts on, e.g. ("parent job 3", "team-a"). At server
            side, the request keeps that workspace.

    Returns:
        - The new copy of dag after applying the policy
        - The new copy of skypilot config after applying the policy. At server
          side, its `active_workspace` is the workspace the request runs in.
    """
    if isinstance(entrypoint, task_lib.Task):
        dag = dag_lib.Dag()
        dag.add(entrypoint)
    else:
        dag = entrypoint

    policy_location = skypilot_config.get_nested(('admin_policy',), None)
    policy = _get_policy_impl(policy_location)
    if policy is None:
        return dag, skypilot_config.resolved_config()

    user = None
    client_api_version = None
    client_version = None
    if at_client_side:
        logger.info(f'Applying client admin policy: {policy}')
    else:
        # When being called by the server, the middleware has set the
        # current user and this information is available at this point.
        user = common_utils.get_current_user()
        # Retrieve client version info from context vars (set by middleware)
        client_api_version = versions.get_remote_api_version()
        client_version = versions.get_remote_version()
        logger.info(f'Applying server admin policy: {policy}')
    config = copy.deepcopy(skypilot_config.resolved_config())
    mutated_dag = dag_lib.Dag()
    mutated_dag.name = dag.name
    # Preserve DAG execution properties if set
    if dag.is_job_group():
        assert dag.execution is not None
        mutated_dag.set_execution(dag.execution)
        # Preserve the rest of the JobGroup header: these live on the Dag,
        # not on the tasks the policy mutates, so a fresh Dag would
        # silently drop them (e.g., an explicit `inter_connection: false`
        # would revert to unset, which behaves as enabled).
        mutated_dag.inter_connection = dag.inter_connection
        mutated_dag.primary_tasks = dag.primary_tasks
        mutated_dag.termination_delay = dag.termination_delay

    mutated_config = None
    for task in dag.tasks:
        user_request = admin_policy.UserRequest(
            task,
            config,
            request_name,
            request_options,
            at_client_side,
            user,
            client_api_version=client_api_version,
            client_version=client_version,
        )
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
    if not at_client_side:
        assert user is not None
        _route_workspace(mutated_config, request_name, request_options, user,
                         existing_resource)

    # Update the new_dag's graph with the old dag's graph
    for u, v in dag.graph.edges:
        u_idx = dag.tasks.index(u)
        v_idx = dag.tasks.index(v)
        mutated_dag.graph.add_edge(mutated_dag.tasks[u_idx],
                                   mutated_dag.tasks[v_idx])

    # MutatedUserRequest carries the whole SkyPilot config, so its repr would
    # print api_server.service_account_token and any docker-login password in
    # full. Log a copy whose config is redacted; dataclasses.replace keeps the
    # repr shape, so this keeps working if the request grows a field.
    logger.debug(
        'Mutated user request: %s',
        dataclasses.replace(mutated_user_request,
                            skypilot_config=config_utils.Config(
                                config_utils.redact_sensitive_values(
                                    mutated_user_request.skypilot_config))))
    mutated_dag.policy_applied = True
    return mutated_dag, mutated_config
