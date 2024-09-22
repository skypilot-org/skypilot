import copy
import getpass

import sky


class TaskLabelPolicy(sky.AdminPolicy):
    """Example policy: adds a label of the local user name to all tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Adds a label for task with the local user name."""
        local_user_name = getpass.getuser()

        # Adds a label for task with the local user name
        task = user_request.task
        for r in task.resources:
            r.labels['local_user'] = local_user_name

        return sky.MutatedUserRequest(
            task=task, skypilot_config=user_request.skypilot_config)


class ConfigLabelPolicy(sky.AdminPolicy):
    """Example policy: adds a label for skypilot_config with local user name."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Adds a label for skypilot_config with local user name."""
        local_user_name = getpass.getuser()

        # Adds label for skypilot_config with the local user name
        skypilot_config = copy.deepcopy(user_request.skypilot_config)
        skypilot_config.set_nested(('gcp', 'labels', 'local_user'),
                                   local_user_name)
        return sky.MutatedUserRequest(task=user_request.task,
                                      skypilot_config=skypilot_config)


class RejectAllPolicy(sky.AdminPolicy):
    """Example policy: rejects all user requests."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Rejects all user requests."""
        del user_request
        raise RuntimeError('Reject all policy')


class EnforceAutostopPolicy(sky.AdminPolicy):
    """Example policy: enforce autostop for all tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Enforces autostop for all tasks."""
        request_options = user_request.request_options
        # Request options is None when a task is executed with `jobs launch` or
        # `sky serve up`.
        if request_options is None:
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)
        idle_minutes_to_autostop = request_options.idle_minutes_to_autostop
        # Enforce autostop/down to be set for all tasks for new clusters.
        if not request_options.cluster_running and (
                idle_minutes_to_autostop is None or
                idle_minutes_to_autostop < 0):
            raise RuntimeError('Autostop/down must be set for all newly '
                               'launched clusters.')
        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)
