import getpass

from sky import MutatedUserTask
from sky import UserTask


def task_label_policy(user_task: UserTask) -> MutatedUserTask:
    """Example policy."""
    local_user_name = getpass.getuser()

    # Add label for task with the local user name
    task = user_task.task
    for r in task.resources:
        r.labels['local_user'] = local_user_name

    return MutatedUserTask(task=task, skypilot_config=user_task.skypilot_config)


def config_label_policy(user_task: UserTask) -> MutatedUserTask:
    """Example policy."""
    local_user_name = getpass.getuser()

    # Add label for skypilot_config with the local user name
    skypilot_config = user_task.skypilot_config
    if not skypilot_config.get('aws'):
        skypilot_config['aws'] = {}
    labels = skypilot_config['aws'].get('labels', {})
    labels['local_user'] = local_user_name
    skypilot_config['aws']['labels'] = labels

    return MutatedUserTask(task=user_task.task, skypilot_config=skypilot_config)
