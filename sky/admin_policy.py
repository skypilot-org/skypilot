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
        cluster_name: Name of the cluster to create/reuse. It is None if not
            specified by the user.
        idle_minutes_to_autostop: Autostop setting requested by a user. The
            cluster will be set to autostop after this many minutes of idleness.
        down: If true, use autodown rather than autostop.
        dryrun: Is the request a dryrun?
    """
    cluster_name: Optional[str]
    idle_minutes_to_autostop: Optional[int]
    down: bool
    dryrun: bool


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
