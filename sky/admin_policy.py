"""Interface for admin-defined policy for user requests."""
import abc
import dataclasses
import typing
from typing import Optional

if typing.TYPE_CHECKING:
    import sky


@dataclasses.dataclass
class RequestOptions:
    """Request options for admin policy.

    Args:
        cluster_name: Name of the cluster to create/reuse.
        cluster_running: Whether the cluster is running.
        idle_minutes_to_autostop: If provided, the cluster will be set to
            autostop after this many minutes of idleness.
        down: If true, use autodown rather than autostop.
        dryrun: Is the request a dryrun?
    """
    cluster_name: Optional[str]
    cluster_running: bool
    idle_minutes_to_autostop: Optional[int]
    down: bool
    dryrun: bool


@dataclasses.dataclass
class UserRequest:
    """User request to the policy.

    It is a combination of a task, request options, and the global skypilot
    config used to run a task, including `sky launch / exec / jobs launch / ..`.

    Args:
        task: User specified task.
        skypilot_config: Global skypilot config to be used in this request.
        request_options: Request options. It can be None for jobs and
            services.
    """
    task: 'sky.Task'
    skypilot_config: 'sky.Config'
    request_options: Optional['RequestOptions'] = None


@dataclasses.dataclass
class MutatedUserRequest:
    task: 'sky.Task'
    skypilot_config: 'sky.Config'


# pylint: disable=line-too-long
class AdminPolicy:
    """Abstract interface of an admin-defined policy for all user requests.

    A policy is a string to a python class inheriting from AdminPolicy that can
    be imported from the same environment where SkyPilot is running.


    Admins can implement a subclass of AdminPolicy with the following signature:

        import sky

        class SkyPilotPolicyV1(sky.AdminPolicy):
            def validate_and_mutate(user_request: UserRequest) -> MutatedUserRequest:
                ...
                return MutatedUserRequest(task=..., skypilot_config=...)

    The policy can mutate both task and skypilot_config.

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
                MutatedUserRequest contains (sky.Task, sky.Config)

        Raises:
            Exception to throw if the user request failed the validation.
        """
        raise NotImplementedError(
            'Your policy must implement validate_and_mutate')
