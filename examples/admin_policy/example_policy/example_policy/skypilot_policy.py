import getpass

import sky


class TaskLabelPolicy(sky.AdminPolicy):
    """Example policy: add label for task with the local user name."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
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
        local_user_name = getpass.getuser()

        # Add label for skypilot_config with the local user name
        skypilot_config = user_request.skypilot_config
        if skypilot_config.get('gcp') is None:
            skypilot_config['gcp'] = {}
        labels = skypilot_config['gcp'].get('labels', {})
        labels['local_user'] = local_user_name
        skypilot_config['gcp']['labels'] = labels

        return sky.MutatedUserRequest(task=user_request.task,
                                      skypilot_config=skypilot_config)
