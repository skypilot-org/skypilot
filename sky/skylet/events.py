"""skylet events"""
import math
import os
import re
import subprocess
import sys
import time
import traceback

import psutil
import yaml

from sky import clouds
from sky import sky_logging
from sky.backends import cloud_vm_ray_backend
from sky.clouds import cloud_registry
from sky.serve import serve_utils
from sky.skylet import autostop_lib
from sky.skylet import job_lib
from sky.spot import spot_utils
from sky.utils import cluster_yaml_utils
from sky.utils import common_utils
from sky.utils import ux_utils

# Seconds of sleep between the processing of skylet events.
EVENT_CHECKING_INTERVAL_SECONDS = 20
logger = sky_logging.init_logger(__name__)


class SkyletEvent:
    """Skylet event.

    The event is triggered every EVENT_INTERVAL_SECONDS seconds.

    Usage: override the EVENT_INTERVAL_SECONDS and _run method in subclass.
    """
    # Run this event every this many seconds.
    EVENT_INTERVAL_SECONDS = -1

    def __init__(self):
        self._event_interval = int(
            math.ceil(self.EVENT_INTERVAL_SECONDS /
                      EVENT_CHECKING_INTERVAL_SECONDS))
        self._n = 0

    def run(self):
        self._n = (self._n + 1) % self._event_interval
        if self._n % self._event_interval == 0:
            logger.debug(f'{self.__class__.__name__} triggered')
            try:
                self._run()
            except Exception as e:  # pylint: disable=broad-except
                # Keep the skylet running even if an event fails.
                logger.error(f'{self.__class__.__name__} error: {e}')
                with ux_utils.enable_traceback():
                    logger.error(traceback.format_exc())

    def _run(self):
        raise NotImplementedError


class JobSchedulerEvent(SkyletEvent):
    """Skylet event for scheduling jobs"""
    EVENT_INTERVAL_SECONDS = 300

    def _run(self):
        job_lib.scheduler.schedule_step()


class SpotJobUpdateEvent(SkyletEvent):
    """Skylet event for updating spot job status."""
    EVENT_INTERVAL_SECONDS = 300

    def _run(self):
        spot_utils.update_spot_job_status()


class ServiceUpdateEvent(SkyletEvent):
    """Skylet event for updating sky serve service status.

    This is needed to handle the case that controller process is somehow
    terminated and the service status is not updated.
    """
    EVENT_INTERVAL_SECONDS = 300

    def _run(self):
        serve_utils.update_service_status()


class AutostopEvent(SkyletEvent):
    """Skylet event for autostop.

    Idleness timer gets set to 0 whenever:
      - A first autostop setting is set. By "first", either there's never any
        autostop setting set, or the last autostop setting is a cancel (idle
        minutes < 0); or
      - This event wakes up and job_lib.is_cluster_idle() returns False; or
      - The cluster has restarted; or
      - A job is submitted (handled in the backend; not here).
    """
    EVENT_INTERVAL_SECONDS = 60

    _UPSCALING_PATTERN = re.compile(r'upscaling_speed: (\d+)')
    _CATCH_NODES = re.compile(r'cache_stopped_nodes: (.*)')

    def __init__(self):
        super().__init__()
        autostop_lib.set_last_active_time_to_now()

    def _run(self):
        autostop_config = autostop_lib.get_autostop_config()

        if (autostop_config.autostop_idle_minutes < 0 or
                autostop_config.boot_time != psutil.boot_time()):
            autostop_lib.set_last_active_time_to_now()
            logger.debug('autostop_config not set. Skipped.')
            return

        if job_lib.is_cluster_idle():
            idle_minutes = (time.time() -
                            autostop_lib.get_last_active_time()) // 60
            logger.debug(
                f'Idle minutes: {idle_minutes}, '
                f'AutoStop config: {autostop_config.autostop_idle_minutes}')
        else:
            autostop_lib.set_last_active_time_to_now()
            idle_minutes = -1
            logger.debug(
                'Not idle. Reset idle minutes.'
                f'AutoStop config: {autostop_config.autostop_idle_minutes}')
        if idle_minutes >= autostop_config.autostop_idle_minutes:
            logger.info(
                f'{idle_minutes} idle minutes reached; threshold: '
                f'{autostop_config.autostop_idle_minutes} minutes. Stopping.')
            self._stop_cluster(autostop_config)

    def _stop_cluster(self, autostop_config):
        if (autostop_config.backend ==
                cloud_vm_ray_backend.CloudVmRayBackend.NAME):
            autostop_lib.set_autostopping_started()

            config_path = os.path.abspath(
                os.path.expanduser(
                    cluster_yaml_utils.SKY_CLUSTER_YAML_REMOTE_PATH))
            config = common_utils.read_yaml(config_path)
            provider_name = cluster_yaml_utils.get_provider_name(config)
            cloud = cloud_registry.CLOUD_REGISTRY.from_str(provider_name)
            assert cloud is not None, f'Unknown cloud: {provider_name}'

            if (cloud.PROVISIONER_VERSION >= clouds.ProvisionerVersion.
                    RAY_PROVISIONER_SKYPILOT_TERMINATOR):
                logger.info('Using new provisioner to stop the cluster.')
                self._stop_cluster_with_new_provisioner(autostop_config, config,
                                                        provider_name)
                return
            logger.info('Not using new provisioner to stop the cluster. '
                        f'Cloud of this cluster: {provider_name}')

            is_cluster_multinode = config['max_workers'] > 0

            # Even for !is_cluster_multinode, we want to call this to replace
            # cache_stopped_nodes.
            self._replace_yaml_for_stopping(config_path, autostop_config.down)

            # Use environment variables to disable the ray usage collection (to
            # avoid overheads and potential issues with the usage) as sdk does
            # not take the argument for disabling the usage collection.
            #
            # Also clear any cloud-specific credentials set as env vars (e.g.,
            # AWS's two env vars). Reason: for single-node AWS SSO clusters, we
            # have seen a weird bug where user image's /etc/profile.d may
            # contain the two AWS env vars, and so they take effect in the
            # bootstrap phase of each of these 3 'ray' commands, throwing a
            # RuntimeError when some private VPC is not found (since the VPC
            # only exists in the assumed role, not in the custome principal set
            # by the env vars).  See #1880 for details.
            env = dict(os.environ, RAY_USAGE_STATS_ENABLED='0')
            env.pop('AWS_ACCESS_KEY_ID', None)
            env.pop('AWS_SECRET_ACCESS_KEY', None)

            # We do "initial ray up + ray down --workers-only" only for
            # multinode clusters as they are not needed for single-node.
            if is_cluster_multinode:
                # `ray up` is required to reset the upscaling speed and min/max
                # workers. Otherwise, `ray down --workers-only` will
                # continuously scale down and up.
                logger.info('Running ray up.')
                script = (cloud_vm_ray_backend.
                          write_ray_up_script_with_patched_launch_hash_fn(
                              config_path,
                              ray_up_kwargs={'restart_only': True}))
                # Passing env inherited from os.environ is technically not
                # needed, because we call `python <script>` rather than `ray
                # <cmd>`. We just need the {RAY_USAGE_STATS_ENABLED: 0} part.
                subprocess.run([sys.executable, script], check=True, env=env)

                logger.info('Running ray down.')
                # Stop the workers first to avoid orphan workers.
                subprocess.run(
                    ['ray', 'down', '-y', '--workers-only', config_path],
                    check=True,
                    # We pass env inherited from os.environ due to calling `ray
                    # <cmd>`.
                    env=env)

            logger.info('Running final ray down.')
            subprocess.run(
                ['ray', 'down', '-y', config_path],
                check=True,
                # We pass env inherited from os.environ due to calling `ray
                # <cmd>`.
                env=env)
        else:
            raise NotImplementedError

    def _stop_cluster_with_new_provisioner(self, autostop_config,
                                           cluster_config, provider_name):
        # pylint: disable=import-outside-toplevel
        from sky import provision as provision_lib
        autostop_lib.set_autostopping_started()

        cluster_name_on_cloud = cluster_config['cluster_name']
        is_cluster_multinode = cluster_config['max_workers'] > 0

        os.environ.pop('AWS_ACCESS_KEY_ID', None)
        os.environ.pop('AWS_SECRET_ACCESS_KEY', None)

        # Stop the ray autoscaler to avoid scaling up, during
        # stopping/terminating of the cluster.
        logger.info('Stopping the ray cluster.')
        subprocess.run('ray stop', shell=True, check=True)

        operation_fn = provision_lib.stop_instances
        if autostop_config.down:
            operation_fn = provision_lib.terminate_instances

        if is_cluster_multinode:
            logger.info('Terminating worker nodes first.')
            operation_fn(provider_name=provider_name,
                         cluster_name_on_cloud=cluster_name_on_cloud,
                         provider_config=cluster_config['provider'],
                         worker_only=True)
        logger.info('Terminating head node.')
        operation_fn(provider_name=provider_name,
                     cluster_name_on_cloud=cluster_name_on_cloud,
                     provider_config=cluster_config['provider'])

    def _replace_yaml_for_stopping(self, yaml_path: str, down: bool):
        with open(yaml_path, 'r', encoding='utf-8') as f:
            yaml_str = f.read()
        yaml_str = self._UPSCALING_PATTERN.sub(r'upscaling_speed: 0', yaml_str)
        if down:
            yaml_str = self._CATCH_NODES.sub(r'cache_stopped_nodes: false',
                                             yaml_str)
        else:
            yaml_str = self._CATCH_NODES.sub(r'cache_stopped_nodes: true',
                                             yaml_str)
        config = yaml.safe_load(yaml_str)
        # Set the private key with the existed key on the remote instance.
        config['auth']['ssh_private_key'] = '~/ray_bootstrap_key.pem'
        # NOTE: We must do this, otherwise with ssh_proxy_command still under
        # 'auth:', `ray up ~/.sky/sky_ray.yaml` on the head node will fail (in
        # general, the clusters do not need or have the proxy set up).
        #
        # Note also that this is ok only because in the local client ->
        # provision head node code path, we have monkey patched
        # hash_launch_conf() to exclude ssh_proxy_command from the hash
        # calculation for the head node. Therefore when this current code is
        # run again on the head, the hash would match the one at head's
        # creation (otherwise the head node would be stopped and a new one
        # would be launched).
        config['auth'].pop('ssh_proxy_command', None)
        # Empty the file_mounts.
        config['file_mounts'] = {}
        common_utils.dump_yaml(yaml_path, config)
        logger.debug('Replaced upscaling speed to 0.')
