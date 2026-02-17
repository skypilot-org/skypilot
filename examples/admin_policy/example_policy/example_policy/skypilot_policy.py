"""Example prebuilt admin policies."""
import subprocess
from typing import Dict, List

import sky
from sky.schemas.api import responses
from sky.utils import common


class DoNothingPolicy(sky.AdminPolicy):
    """Example policy: do nothing."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Returns the user request unchanged."""
        return sky.MutatedUserRequest(user_request.task,
                                      user_request.skypilot_config)


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


class AddLabelsConditionalPolicy(sky.AdminPolicy):
    """Example policy: adds a kubernetes label for skypilot_config
    if the request is a cluster launch request."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        if user_request.request_name in [
                sky.AdminPolicyRequestName.VALIDATE,
                sky.AdminPolicyRequestName.OPTIMIZE
        ]:
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)
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
        if config.get_nested(('aws', 'vpc_names'), None) is None:
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

        Since we refresh the cluster status with `sky.status` whenever this
        policy is applied, we should expect a few seconds latency when a user
        run a request.
        """
        if user_request.request_name not in [
                sky.AdminPolicyRequestName.CLUSTER_LAUNCH,
                sky.AdminPolicyRequestName.CLUSTER_EXEC,
        ]:
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)

        request_options = user_request.request_options
        # Request options is not None when a task is executed with `sky launch`.
        assert request_options is not None
        # Get the cluster record to operate on.
        cluster_name = request_options.cluster_name
        cluster_records: List[responses.StatusResponse] = []
        if cluster_name is not None:
            try:
                cluster_records = sky.get(
                    sky.status([cluster_name],
                               refresh=common.StatusRefreshMode.AUTO,
                               all_users=True))
            except Exception as e:
                raise RuntimeError('Failed to get cluster status for '
                                   f'{cluster_name}: {e}') from None

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


class SetMaxAutostopIdleMinutesPolicy(sky.AdminPolicy):
    """Example policy: set max autostop idle minutes for all tasks."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Sets max autostop idle minutes for all tasks."""
        max_idle_minutes = 10
        task = user_request.task
        resources = task.get_resource_config()
        if 'autostop' not in resources:
            # autostop is disabled
            resources['autostop'] = {'idle_minutes': max_idle_minutes}
        elif ('idle_minutes' not in resources['autostop'] or
              int(resources['autostop']['idle_minutes']) > max_idle_minutes):
            # Autostop idle minutes is too long
            resources['autostop']['idle_minutes'] = max_idle_minutes
        task.set_resources(resources)

        return sky.MutatedUserRequest(
            task=task, skypilot_config=user_request.skypilot_config)


class TokenBucketRateLimiter:
    """Token bucket rate limiter.

    This rate limiter allows a user to make requests up to
    fill_rate requests per second.

    Args:
        capacity: The maximum number of requests allowed in the bucket.
        fill_rate: The rate at which the bucket is filled with requests.

    Example:
        .. code-block:: python

            rate_limiter = TokenBucketRateLimiter(capacity=2, fill_rate=1)
            # The first two calls use up the two tokens in the bucket.
            assert rate_limiter.allow_request('user1') is True
            assert rate_limiter.allow_request('user1') is True
            # The third call is denied as the bucket is empty.
            assert rate_limiter.allow_request('user1') is False
            # Wait for 1 second, the bucket is refilled with 1 token.
            time.sleep(1)
            assert rate_limiter.allow_request('user1') is True
    """

    def __init__(self, capacity, fill_rate):
        """Initializes the token bucket rate limiter.

        Args:
            capacity: The maximum number of requests allowed in the bucket.
            fill_rate: The rate at which the bucket is filled with requests.
        """
        # import the modules here so users importing this module
        # to use other policies do not need to import these modules.
        # pylint: disable=import-outside-toplevel
        import threading

        import sqlalchemy
        from sqlalchemy import orm
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.dialects import sqlite

        self.capacity = float(capacity)
        self.fill_rate = float(fill_rate)  # tokens per second
        self.lock = threading.Lock()
        # Tip: you can swap out the connection string
        # to use a postgres database.
        self._db_engine = sqlalchemy.create_engine('sqlite:///rate_limit.db')
        if self._db_engine.dialect.name == 'sqlite':
            self.insert_func = sqlite.insert
        elif self._db_engine.dialect.name == 'postgresql':
            self.insert_func = postgresql.insert

        self.rate_limit_table = sqlalchemy.Table(
            'rate_limit',
            orm.declarative_base().metadata,
            sqlalchemy.Column('user_name', sqlalchemy.Text, primary_key=True),
            sqlalchemy.Column('tokens', sqlalchemy.REAL),
            sqlalchemy.Column('last_refill_time', sqlalchemy.REAL),
        )

        with orm.Session(self._db_engine) as init_session:
            init_session.execute(
                sqlalchemy.text('CREATE TABLE IF NOT EXISTS rate_limit '
                                '(user_name TEXT PRIMARY KEY, tokens REAL, '
                                'last_refill_time REAL)'))
            init_session.commit()

    def allow_request(self, user_name):
        """Determines if a request is allowed for a given user.

        Args:
            user_name: The name of the user.

        Returns:
            True if the request is allowed, False otherwise.
        """
        # import the modules here so users importing this module
        # to use other policies do not need to import these modules.
        # pylint: disable=import-outside-toplevel
        import time

        from sqlalchemy import orm

        with self.lock:
            now = time.time()
            with orm.Session(self._db_engine) as session:
                # with_for_update() locks the row until commit() or rollback()
                # is called, or until the code escapes the with block.
                result = session.query(self.rate_limit_table).filter(
                    self.rate_limit_table.c.user_name ==
                    user_name).with_for_update().first()
                if result:
                    tokens = result.tokens
                    last_refill_time = result.last_refill_time
                else:
                    tokens = self.capacity
                    last_refill_time = now
                time_elapsed = now - last_refill_time
                # Refill the bucket based on the fill rate and the time elapsed
                # since the last refill.
                tokens = min(self.capacity,
                             tokens + time_elapsed * self.fill_rate)
                # Check if the request is allowed.
                if tokens >= 1:
                    tokens -= 1
                    allowed = True
                else:
                    allowed = False
                # update the bucket in the database.
                insert_or_update_stmt = (self.insert_func(
                    self.rate_limit_table).values(
                        user_name=user_name,
                        tokens=tokens,
                        last_refill_time=now).on_conflict_do_update(
                            index_elements=[self.rate_limit_table.c.user_name],
                            set_={
                                self.rate_limit_table.c.tokens: tokens,
                                self.rate_limit_table.c.last_refill_time: now
                            }))
                session.execute(insert_or_update_stmt)
                session.commit()
            return allowed


class RateLimitLaunchPolicy(sky.AdminPolicy):
    """Example policy: rate limit cluster launch requests."""
    _RATE_LIMITER = TokenBucketRateLimiter(capacity=10, fill_rate=1)

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Rate limit cluster launch requests."""
        if (user_request.at_client_side or user_request.request_name !=
                sky.AdminPolicyRequestName.CLUSTER_LAUNCH):
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)

        # user is not None when the policy is applied at the server-side
        assert user_request.user is not None
        user_name = user_request.user.name
        if not cls._RATE_LIMITER.allow_request(user_name):
            raise RuntimeError(f'Rate limit exceeded for user {user_name}')

        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)


def update_current_kubernetes_clusters_from_registry():
    """Mock implementation of updating kubernetes clusters from registry."""
    # All cluster names can be fetched from an organization's internal API.
    new_cluster_names = ['my-cluster']
    for cluster_name in new_cluster_names:
        # Update the local kubeconfig with the new cluster credentials.
        subprocess.run(
            f'gcloud container clusters get-credentials {cluster_name} '
            '--region us-central1-c',
            shell=True,
            check=False)


def get_allowed_contexts():
    """Mock implementation of getting allowed kubernetes contexts."""
    # pylint: disable=import-outside-toplevel
    from sky.provision.kubernetes import utils
    contexts = utils.get_all_kube_context_names()
    return contexts[:2]


class DynamicKubernetesContextsUpdatePolicy(sky.AdminPolicy):
    """Example policy: update the kubernetes context to use."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Updates the kubernetes context to use."""
        # Append any new kubernetes clusters in local kubeconfig. An example
        # implementation of this method can be:
        #  1. Query an organization's internal Kubernetes cluster registry,
        #     which can be some internal API, or a secret vault.
        #  2. Append the new credentials to the local kubeconfig.
        update_current_kubernetes_clusters_from_registry()
        # Get the allowed contexts for the user. Similarly, it can retrieve
        # the latest allowed contexts from an organization's internal API.
        allowed_contexts = get_allowed_contexts()

        # Update the kubernetes allowed contexts in skypilot config.
        config = user_request.skypilot_config
        config.set_nested(('kubernetes', 'allowed_contexts'), allowed_contexts)
        return sky.MutatedUserRequest(task=user_request.task,
                                      skypilot_config=config)


class AddVolumesPolicy(sky.AdminPolicy):
    """Example policy: add volumes to the task."""

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        task = user_request.task
        if task.is_controller_task():
            # Skip applying admin policy to job/serve controller
            return sky.MutatedUserRequest(task, user_request.skypilot_config)
        # Use `task.set_volumes` to set the volumes.
        # Or use `task.update_volumes` to update in-place
        # instead of overwriting.
        task.set_volumes({'/mnt/data0': 'pvc0'})
        return sky.MutatedUserRequest(task, user_request.skypilot_config)


class GPUStaticQuotaPolicy(sky.AdminPolicy):
    """Example policy: Enforce a static GPU quota
    for each user for cluster launch requests."""

    # GPU quota allotted for each user.
    GPU_QUOTA_PER_USER = {
        'H100': 2,
        'L40S': 10,
    }

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Enforce a static GPU quota for each user for cluster launch requests.

        This policy is does not enforce a quota for jobs launch requests.

        Note: This policy calls sky.status() to get the total number
        of GPUs currently used by the user and therefore adds a
        few seconds of latency for every cluster launch request.

        Raises:
            RuntimeError: If the user has exceeded the GPU quota for any
            accelerator type.
        """
        # Import ast here so users importing this module
        # to use other policies do not need to import this module.
        # pylint: disable=import-outside-toplevel
        import ast

        # If the request is at client side or not a cluster launch request,
        # do not enforce GPU quota.
        if (user_request.at_client_side or user_request.request_name !=
                sky.AdminPolicyRequestName.CLUSTER_LAUNCH):
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)

        assert user_request.user is not None, (
            'Failed to get user initiating the request.')
        user_name = user_request.user.name
        assert user_name is not None, (
            'Failed to get user name initiating the request.')

        # Get the total number of GPUs currently used by the user.
        try:
            cluster_records = sky.get(
                sky.status(refresh=common.StatusRefreshMode.NONE,
                           all_users=True,
                           _summary_response=True))
        except Exception as e:
            raise RuntimeError('Failed to get cluster records for '
                               f'all users: {e}') from None
        accelerators_used: Dict[str, int] = {}
        cluster_records_for_user = [
            record for record in cluster_records
            if record.user_name == user_name
        ]
        for record in cluster_records_for_user:
            if not record.accelerators:
                continue
            accelerators = ast.literal_eval(record.accelerators)
            for accelerator, count in accelerators.items():
                accelerators_used[accelerator] = accelerators_used.get(
                    accelerator, 0) + (count * record.nodes)
        # At this point, accelerators_used is a dictionary of the
        # GPUs currently used by the user in the format of
        # {accelerator_type: count}.

        # Now, check if any resource request exceeds the GPU quota.
        for resource in user_request.task.resources:
            if resource.accelerators:
                for accelerator, count in resource.accelerators.items():
                    count *= user_request.task.num_nodes
                    quota = cls.GPU_QUOTA_PER_USER.get(accelerator, 0)
                    if accelerators_used.get(accelerator, 0) + count > quota:
                        raise RuntimeError(
                            f'User {user_name} has exceeded the'
                            f'GPU quota for {accelerator}. '
                            f'In use: {accelerators_used.get(accelerator, 0)}, '
                            f'Requested: {count}, '
                            f'Quota: {quota}')

        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)


class RejectOldClientsPolicy(sky.AdminPolicy):
    """Example policy: reject clients with an old API version.

    This policy demonstrates how to use the client version information
    to enforce minimum client versions. This is useful for ensuring all
    users are running a compatible version of SkyPilot.

    The policy checks the client's API version and rejects requests from
    clients that are below the minimum required version.
    """

    # Minimum required API version. Clients with a lower version will be
    # rejected. This should be updated when breaking changes are introduced
    # that require all clients to upgrade.
    MIN_REQUIRED_API_VERSION = 25

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Reject requests from clients with an old API version."""
        # Version check is only applied at the server-side.
        if not user_request.at_client_side:
            # Check client API version
            if user_request.client_api_version is not None:
                if user_request.client_api_version < cls.MIN_REQUIRED_API_VERSION:
                    raise RuntimeError(
                        f'Client API version {user_request.client_api_version} '
                        f'is below the minimum required version '
                        f'{cls.MIN_REQUIRED_API_VERSION}. '
                        f'Please upgrade your SkyPilot client. '
                        f'Your client version: {user_request.client_version}')
            else:
                # client_api_version is None for very old clients that don't send
                # version headers. You may choose to reject these clients as well.
                raise RuntimeError(
                    'Client version information is not available. '
                    'Please upgrade your SkyPilot client to a recent version.')

        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)
