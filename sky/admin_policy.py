"""Interface for admin-defined policy for user requests."""
import abc
import dataclasses
import typing
from typing import Optional

if typing.TYPE_CHECKING:
    import sky


@dataclasses.dataclass
class OperationArgs:
    cluster_name: Optional[str]
    cluster_exists: bool
    idle_minutes_to_autostop: Optional[int]
    down: bool
    dryrun: bool


@dataclasses.dataclass
class UserRequest:
    """User request to the policy.

    Args:
        task: User specified task.
        skypilot_config: Global skypilot config.
        execution_args: Execution arguments. It can be None for jobs and
            services.
    """
    task: 'sky.Task'
    skypilot_config: 'sky.NestedConfig'
    operation_args: Optional['OperationArgs'] = None


@dataclasses.dataclass
class MutatedUserRequest:
    task: 'sky.Task'
    skypilot_config: 'sky.NestedConfig'


# pylint: disable=line-too-long
class AdminPolicy:
    """Interface for admin-defined policy for user requests.

    A user-defined policy is a string to a python function that can be imported
    from the same environment where SkyPilot is running.

    It can be specified in the SkyPilot config file under the key 'policy', e.g.

        policy: my_package.SkyPilotPolicyV1

    The AdminPolicy class is expected to have the following signature:

        import sky

        class SkyPilotPolicyV1(sky.AdminPolicy):
            def validate_and_mutate(user_request: UserRequest) -> MutatedUserRequest:
                ...
                return MutatedUserRequest(task=..., skypilot_config=...)

    The function can mutate both task and skypilot_config.
    """

    @classmethod
    @abc.abstractmethod
    def validate_and_mutate(cls,
                            user_request: UserRequest) -> MutatedUserRequest:
        """Validates and mutates the user request and returns mutated request.

        Args:
            user_request: The user request to validate and mutate.
                UserRequest contains (sky.Task, sky.NestedConfig)

        Returns:
            MutatedUserRequest: The mutated user request.
                MutatedUserRequest contains (sky.Task, sky.NestedConfig)

        Raises:
            Any exception to reject the user request.
        """
        raise NotImplementedError(
            'Your policy must implement validate_and_mutate')
