"""Example prebuilt admin policies."""
import sky


class RejectAllPolicy(sky.AdminPolicy):
    """Example policy: rejects all user requests."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Rejects all user requests."""
        raise RuntimeError('Reject all policy')


class AddLabelsPolicy(sky.AdminPolicy):
    """Example policy: adds a kubernetes label for skypilot_config."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        config = user_request.skypilot_config
        labels = config.get_nested(('kubernetes', 'custom_metadata', 'labels'),
                                   {})
        labels['app'] = 'skypilot'
        config.set_nested(('kubernetes', 'custom_metadata', 'labels'), labels)
        return sky.MutatedUserRequest(user_request.task, config)


class DisablePublicIpPolicy(sky.AdminPolicy):
    """Example policy: disables public IP for all AWS tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        config = user_request.skypilot_config
        config.set_nested(('aws', 'use_internal_ip'), True)
        if config.get_nested(('aws', 'vpc_name'), None) is None:
            # If no VPC name is specified, it is likely a mistake. We should
            # reject the request
            raise RuntimeError('VPC name should be set. Check organization '
                               'wiki for more information.')
        return sky.MutatedUserRequest(user_request.task, config)


class UseSpotForGpuPolicy(sky.AdminPolicy):
    """Example policy: use spot instances for all GPU tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Sets use_spot to True for all GPU tasks."""
        task = user_request.task
        new_resources = []
        for r in task.resources:
            if r.accelerators:
                new_resources.append(r.copy(use_spot=True))
            else:
                new_resources.append(r)

        task.set_resources(type(task.resources)(new_resources))

        return sky.MutatedUserRequest(
            task=task, skypilot_config=user_request.skypilot_config)


class EnforceAutostopPolicy(sky.AdminPolicy):
    """Example policy: enforce autostop for all tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Enforces autostop for all tasks.
        
        Note that with this policy enforced, users can still change the autostop
        setting for an existing cluster by using `sky autostop`.
        """
        request_options = user_request.request_options

        # Request options is None when a task is executed with `jobs launch` or
        # `sky serve up`.
        if request_options is None:
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)

        # Get the cluster record to operate on.
        cluster_name = request_options.cluster_name
        cluster_records = []
        if cluster_name is not None:
            cluster_records = sky.status(cluster_name, refresh=True)

        # Check if the user request should specify autostop settings.
        need_autostop = False
        if not cluster_records:
            # Cluster does not exist
            need_autostop = True
        elif cluster_records[0]['status'] == sky.ClusterStatus.STOPPED:
            # Cluster is stopped
            need_autostop = True
        elif cluster_records[0]['autostop'] < 0:
            # Cluster is running but autostop is not set
            need_autostop = True

        # Check if the user request is setting autostop settings.
        is_setting_autostop = False
        idle_minutes_to_autostop = request_options.idle_minutes_to_autostop
        is_setting_autostop = (idle_minutes_to_autostop is not None and
                               idle_minutes_to_autostop >= 0)

        # If the cluster requires autostop but the user request is not setting
        # autostop settings, raise an error.
        if need_autostop and not is_setting_autostop:
            raise RuntimeError('Autostop/down must be set for all clusters.')

        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)
