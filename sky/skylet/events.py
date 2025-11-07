"""skylet events"""
import math
import os
import re
import subprocess
import time
import traceback

import psutil

from sky import clouds
from sky import sky_logging
from sky.backends import cloud_vm_ray_backend
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.serve import serve_utils
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import cluster_utils
from sky.utils import registry
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils

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

    def start(self):
        pass

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
        job_lib.scheduler.schedule_step(force_update_jobs=True)


class ManagedJobEvent(SkyletEvent):
    """Skylet event for updating and scheduling managed jobs."""
    EVENT_INTERVAL_SECONDS = 300

    def start(self):
        cpus_env_var = os.environ.get('SKYPILOT_POD_CPU_CORE_LIMIT')
        if cpus_env_var is not None:
            with open(os.path.expanduser(constants.CONTROLLER_K8S_CPU_FILE),
                      'w',
                      encoding='utf-8') as f:
                f.write(cpus_env_var)
        memory_env_var = os.environ.get('SKYPILOT_POD_MEMORY_GB_LIMIT')
        if memory_env_var is not None:
            with open(os.path.expanduser(constants.CONTROLLER_K8S_MEMORY_FILE),
                      'w',
                      encoding='utf-8') as f:
                f.write(memory_env_var)

    def _run(self):
        if not os.path.exists(
                os.path.expanduser(
                    managed_job_constants.JOB_CONTROLLER_INDICATOR_FILE)):
            # Note: since the skylet is started before the user setup (in
            # jobs-controller.yaml.j2) runs, it's possible that we hit this
            # before the indicator file is written. However, since we will wait
            # EVENT_INTERVAL_SECONDS before the first run, this should be very
            # unlikely.
            logger.info('No jobs controller indicator file found.')
            all_job_ids = managed_job_state.get_all_job_ids_by_name(None)
            if not all_job_ids:
                logger.info('No jobs running. Stopping controllers.')
                # TODO(cooperc): Move this to a shared function also called by
                # sdk.api_stop(). (#7229)
                try:
                    records = scheduler.get_controller_process_records()
                    if records is not None:
                        for record in records:
                            if managed_job_utils.controller_process_alive(
                                    record, quiet=False):
                                subprocess_utils.kill_children_processes(
                                    parent_pids=[record.pid], force=True)
                        os.remove(
                            os.path.expanduser(
                                scheduler.JOB_CONTROLLER_PID_PATH))
                except Exception as e:  # pylint: disable=broad-except
                    # in case we get perm issues or something is messed up, just
                    # ignore it and assume the process is dead
                    logger.error(
                        f'Error looking at job controller pid file: {e}')
                    pass
            logger.info(f'{len(all_job_ids)} jobs running. Assuming the '
                        'indicator file hasn\'t been written yet.')
            return

        logger.info('=== Updating managed job status ===')
        managed_job_utils.update_managed_jobs_statuses()
        scheduler.maybe_start_controllers()


class ServiceUpdateEvent(SkyletEvent):
    """Skylet event for updating sky serve service status.

    This is needed to handle the case that controller process is somehow
    terminated and the service status is not updated.
    """
    EVENT_INTERVAL_SECONDS = 300

    def __init__(self, pool: bool) -> None:
        super().__init__()
        self._pool = pool

    def _run(self):
        serve_utils.update_service_status(self._pool)


class UsageHeartbeatReportEvent(SkyletEvent):
    """Skylet event for reporting usage."""
    EVENT_INTERVAL_SECONDS = 600

    def _run(self):
        usage_lib.send_heartbeat(interval_seconds=self.EVENT_INTERVAL_SECONDS)


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

        ignore_idle_check = (
            autostop_config.wait_for == autostop_lib.AutostopWaitFor.NONE)
        is_idle = True
        if not ignore_idle_check:
            if not job_lib.is_cluster_idle(
            ) or managed_job_state.get_num_alive_jobs() or (
                    autostop_config.wait_for
                    == autostop_lib.AutostopWaitFor.JOBS_AND_SSH and
                    autostop_lib.has_active_ssh_sessions()):
                is_idle = False

        if ignore_idle_check or is_idle:
            minutes_since_last_active = (
                time.time() - autostop_lib.get_last_active_time()) // 60
            logger.debug(
                f'Minutes since last active: {minutes_since_last_active}, '
                f'AutoStop idle minutes: '
                f'{autostop_config.autostop_idle_minutes}, '
                f'Wait for: {autostop_config.wait_for.value}')
        else:
            autostop_lib.set_last_active_time_to_now()
            minutes_since_last_active = -1
            logger.debug('Not idle. Reset idle minutes. '
                         f'AutoStop idle minutes: '
                         f'{autostop_config.autostop_idle_minutes}, '
                         f'Wait for: {autostop_config.wait_for.value}')
        if minutes_since_last_active >= autostop_config.autostop_idle_minutes:
            logger.info(
                f'{minutes_since_last_active} minute(s) since last active; '
                f'threshold: {autostop_config.autostop_idle_minutes} minutes. '
                f'Stopping.')
            self._stop_cluster(autostop_config)

    def _stop_cluster(self, autostop_config):
        if (autostop_config.backend ==
                cloud_vm_ray_backend.CloudVmRayBackend.NAME):
            autostop_lib.set_autostopping_started()

            config_path = os.path.abspath(
                os.path.expanduser(cluster_utils.SKY_CLUSTER_YAML_REMOTE_PATH))
            config = yaml_utils.read_yaml(config_path)
            provider_name = cluster_utils.get_provider_name(config)
            cloud = registry.CLOUD_REGISTRY.from_str(provider_name)
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
                subprocess.run(f'{constants.SKY_PYTHON_CMD} {script}',
                               check=True,
                               shell=True,
                               env=env)

                logger.info('Running ray down.')
                # Stop the workers first to avoid orphan workers.
                subprocess.run(
                    f'{constants.SKY_RAY_CMD} down -y --workers-only '
                    f'{config_path}',
                    check=True,
                    shell=True,
                    # We pass env inherited from os.environ due to calling `ray
                    # <cmd>`.
                    env=env)

            # Stop the ray autoscaler to avoid scaling up, during
            # stopping/terminating of the cluster. We do not rely `ray down`
            # below for stopping ray cluster, as it will not use the correct
            # ray path.
            logger.info('Stopping the ray cluster.')
            subprocess.run(f'{constants.SKY_RAY_CMD} stop',
                           shell=True,
                           check=True)

            logger.info('Running final ray down.')
            subprocess.run(
                f'{constants.SKY_RAY_CMD} down -y {config_path}',
                check=True,
                shell=True,
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

        # Clear AWS credentials from environment to force boto3 to use IAM
        # role attached to the instance (lowest priority in credential chain).
        # This allows the cluster to stop/terminate itself using its IAM role.
        os.environ.pop('AWS_ACCESS_KEY_ID', None)
        os.environ.pop('AWS_SECRET_ACCESS_KEY', None)
        os.environ.pop('AWS_SESSION_TOKEN', None)
        # Point boto3 to /dev/null to skip reading credentials from files.
        os.environ['AWS_SHARED_CREDENTIALS_FILE'] = '/dev/null'
        os.environ['AWS_CONFIG_FILE'] = '/dev/null'

        # Stop the ray autoscaler to avoid scaling up, during
        # stopping/terminating of the cluster.
        logger.info('Stopping the ray cluster.')
        subprocess.run(f'{constants.SKY_RAY_CMD} stop', shell=True, check=True)

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
        config = yaml_utils.safe_load(yaml_str)
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
        yaml_utils.dump_yaml(yaml_path, config)
        logger.debug('Replaced upscaling speed to 0.')
