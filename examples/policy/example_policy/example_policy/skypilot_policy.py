import getpass

from sky import MutatedUserTask
from sky import UserTask


def example_policy_task_label(user_task: UserTask) -> MutatedUserTask:
    """Example policy."""
    local_user_name = getpass.getuser()

    # Add label for task with the local user name
    task = user_task.task
    for r in task.resources:
        r.labels['local_user'] = local_user_name

    return MutatedUserTask(task=task, skypilot_config=user_task.skypilot_config)
