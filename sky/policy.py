"""Interface for admin-defined policy for user requests."""
import abc
import dataclasses
import typing

if typing.TYPE_CHECKING:
    import sky


@dataclasses.dataclass
class UserRequest:
    task: 'sky.Task'
    skypilot_config: 'sky.NestedConfig'


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
        raise NotImplementedError(
            'Your policy must implement validate_and_mutate')
