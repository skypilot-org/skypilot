import copy
import getpass

import sky


class TaskLabelPolicy(sky.AdminPolicy):
    """Example policy: add label for task with the local user name."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Add label for task with the local user name."""
        local_user_name = getpass.getuser()

        # Add label for task with the local user name
        task = user_request.task
        for r in task.resources:
            r.labels['local_user'] = local_user_name

        return sky.MutatedUserRequest(
            task=task, skypilot_config=user_request.skypilot_config)


class ConfigLabelPolicy(sky.AdminPolicy):
    """Example policy: add label for skypilot_config with the local user name."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Add label for skypilot_config with the local user name."""
        local_user_name = getpass.getuser()

        # Add label for skypilot_config with the local user name
        skypilot_config = copy.deepcopy(user_request.skypilot_config)
        skypilot_config.set_nested(('gcp', 'labels', 'local_user'),
                                   local_user_name)
        return sky.MutatedUserRequest(task=user_request.task,
                                      skypilot_config=skypilot_config)


class RejectAllPolicy(sky.AdminPolicy):
    """Example policy: reject all user requests."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Reject all user requests."""
        del user_request
        raise RuntimeError('Reject all policy')
