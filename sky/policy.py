"""Interface for admin-defined policy for user requests."""
import abc
import dataclasses
import typing
from typing import Any, Dict

if typing.TYPE_CHECKING:
    from sky import task as task_lib


@dataclasses.dataclass
class UserRequest:
    task: 'task_lib.Task'
    skypilot_config: Dict[str, Any]


@dataclasses.dataclass
class MutatedUserRequest:
    task: 'task_lib.Task'
    skypilot_config: Dict[str, Any]

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
    def validate_and_mutate(cls, user_request: UserRequest) -> MutatedUserRequest:
        raise NotImplementedError('Your policy must implement validate_and_mutate')
