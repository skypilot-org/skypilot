"""Interface for admin-defined policy for user requests."""
import abc
import dataclasses
import typing
from typing import Any, Dict, Optional

import pydantic

import sky
from sky import exceptions
from sky.adaptors import common as adaptors_common
from sky.utils import config_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')


class RequestOptions(pydantic.BaseModel):
    """Request options for admin policy.

    Args:
        cluster_name: Name of the cluster to create/reuse. It is None if not
            specified by the user.
        idle_minutes_to_autostop: Autostop setting requested by a user. The
            cluster will be set to autostop after this many minutes of idleness.
        down: If true, use autodown rather than autostop.
        dryrun: Is the request a dryrun?
    """
    cluster_name: Optional[str]
    # Keep these two fields for backward compatibility. The values are copied
    # from task.resources.autostop_config, so that legacy admin policy plugins
    # can still read the correct autostop config from request options before
    # we drop the compatibility.
    # TODO(aylei): remove these fields after 0.12.0
    idle_minutes_to_autostop: Optional[int]
    down: bool
    dryrun: bool


class _UserRequestBody(pydantic.BaseModel):
    """Auxiliary model to validate and serialize a user request."""
    task: Dict[str, Any]
    skypilot_config: Dict[str, Any]
    request_options: Optional[RequestOptions] = None
    at_client_side: bool = False


@dataclasses.dataclass
class UserRequest:
    """A user request.

    A "user request" is defined as a `sky launch / exec` command or its API
    equivalent.

    `sky jobs launch / serve up` involves multiple launch requests, including
    the launch of controller and clusters for a job (which can have multiple
    tasks if it is a pipeline) or service replicas. Each launch is a separate
    request.

    This class wraps the underlying task, the global skypilot config used to run
    a task, and the request options.

    Args:
        task: User specified task.
        skypilot_config: Global skypilot config to be used in this request.
        request_options: Request options. It is None for jobs and services.
        at_client_side: Is the request intercepted by the policy at client-side?
    """
    task: 'sky.Task'
    skypilot_config: 'sky.Config'
    request_options: Optional['RequestOptions'] = None
    at_client_side: bool = False

    def encode(self) -> str:
        return _UserRequestBody(
            task=self.task.to_yaml_config(),
            skypilot_config=dict(self.skypilot_config),
            request_options=self.request_options,
            at_client_side=self.at_client_side).model_dump_json()

    @classmethod
    def decode(cls, body: str) -> 'UserRequest':
        user_request_body = _UserRequestBody.model_validate_json(body)
        return cls(task=sky.Task.from_yaml_config(user_request_body.task),
                   skypilot_config=config_utils.Config.from_dict(
                       user_request_body.skypilot_config),
                   request_options=user_request_body.request_options,
                   at_client_side=user_request_body.at_client_side)


class _MutatedUserRequestBody(pydantic.BaseModel):
    """Auxiliary model to validate and serialize a user request."""
    task: Dict[str, Any]
    skypilot_config: Dict[str, Any]


@dataclasses.dataclass
class MutatedUserRequest:
    """Mutated user request."""

    task: 'sky.Task'
    skypilot_config: 'sky.Config'

    def encode(self) -> str:
        return _MutatedUserRequestBody(
            task=self.task.to_yaml_config(),
            skypilot_config=dict(self.skypilot_config)).model_dump_json()

    @classmethod
    def decode(cls, mutated_user_request_body: str) -> 'MutatedUserRequest':
        mutated_user_request_body = _MutatedUserRequestBody.model_validate_json(
            mutated_user_request_body)
        return cls(task=sky.Task.from_yaml_config(
            mutated_user_request_body.task),
                   skypilot_config=config_utils.Config.from_dict(
                       mutated_user_request_body.skypilot_config))


class PolicyInterface:
    """Interface for admin-defined policy for user requests."""

    @abc.abstractmethod
    def apply(self, user_request: UserRequest) -> MutatedUserRequest:
        """Apply the admin policy to the user request."""

    def __str__(self):
        return f'{self.__class__.__name__}'


# pylint: disable=line-too-long
class AdminPolicy(PolicyInterface):
    """Abstract interface of an admin-defined policy for all user requests.

    Admins can implement a subclass of AdminPolicy with the following signature:

        import sky

        class SkyPilotPolicyV1(sky.AdminPolicy):
            def validate_and_mutate(user_request: UserRequest) -> MutatedUserRequest:
                ...
                return MutatedUserRequest(task=..., skypilot_config=...)

    The policy can mutate both task and skypilot_config. Admins then distribute
    a simple module that contains this implementation, installable in a way
    that it can be imported by users from the same Python environment where
    SkyPilot is running.

    Users can register a subclass of AdminPolicy in the SkyPilot config file
    under the key 'admin_policy', e.g.

        admin_policy: my_package.SkyPilotPolicyV1
    """

    @classmethod
    @abc.abstractmethod
    def validate_and_mutate(cls,
                            user_request: UserRequest) -> MutatedUserRequest:
        """Validates and mutates the user request and returns mutated request.

        Args:
            user_request: The user request to validate and mutate.
                UserRequest contains (sky.Task, sky.Config)

        Returns:
            MutatedUserRequest: The mutated user request.

        Raises:
            Exception to throw if the user request failed the validation.
        """
        raise NotImplementedError(
            'Your policy must implement validate_and_mutate')

    def apply(self, user_request: UserRequest) -> MutatedUserRequest:
        return self.validate_and_mutate(user_request)


class PolicyTemplate(PolicyInterface):
    """Admin policy template that can be instantiated to create a policy."""

    @abc.abstractmethod
    def validate_and_mutate(self,
                            user_request: UserRequest) -> MutatedUserRequest:
        """Validates and mutates the user request and returns mutated request.

        Args:
            user_request: The user request to validate and mutate.
                UserRequest contains (sky.Task, sky.Config)

        Returns:
            MutatedUserRequest: The mutated user request.

        Raises:
            Exception to throw if the user request failed the validation.
        """
        raise NotImplementedError(
            'Your policy must implement validate_and_mutate')

    def apply(self, user_request: UserRequest) -> MutatedUserRequest:
        return self.validate_and_mutate(user_request)


class RestfulAdminPolicy(PolicyTemplate):
    """Admin policy that calls a RESTful API for validation."""

    def __init__(self, policy_url: str):
        super().__init__()
        self.policy_url = policy_url

    def validate_and_mutate(self,
                            user_request: UserRequest) -> MutatedUserRequest:
        try:
            response = requests.post(
                self.policy_url,
                json=user_request.encode(),
                headers={'Content-Type': 'application/json'},
                # TODO(aylei): make this configurable
                timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.UserRequestRejectedByPolicy(
                    f'Failed to validate request with admin policy URL '
                    f'{self.policy_url}: {e}') from e

        try:
            mutated_user_request = MutatedUserRequest.decode(response.json())
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.UserRequestRejectedByPolicy(
                    f'Failed to decode response from admin policy URL '
                    f'{self.policy_url}: {e}') from e
        return mutated_user_request
