"""skylet events"""
import math
import os
import psutil
import re
import subprocess
import time
import traceback
import yaml

from sky import sky_logging
from sky.backends import backend_utils, cloud_vm_ray_backend
from sky.skylet import autostop_lib, job_lib

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
                logger.error(traceback.format_exc())

    def _run(self):
        raise NotImplementedError


class JobUpdateEvent(SkyletEvent):
    """Skylet event for updating job status."""
    EVENT_INTERVAL_SECONDS = 20

    def __init__(self):
        super().__init__()
        self.ray_yaml_path = os.path.abspath(
            os.path.expanduser(backend_utils.SKY_RAY_YAML_REMOTE_PATH))

    def _run(self):
        with open(self.ray_yaml_path, 'r') as f:
            config = yaml.safe_load(f)
            cluster_name = config['cluster_name']
        job_lib.update_status(cluster_name)


class AutostopEvent(SkyletEvent):
    """Skylet event for autostop."""
    EVENT_INTERVAL_SECONDS = 60

    _NUM_WORKER_PATTERN = re.compile(r'((?:min|max))_workers: (\d+)')
    _UPSCALING_PATTERN = re.compile(r'upscaling_speed: (\d+)')
    _CATCH_NODES = re.compile(r'cache_stopped_nodes: (.*)')

    def __init__(self):
        super().__init__()
        self.last_active_time = time.time()
        self.ray_yaml_path = os.path.abspath(
            os.path.expanduser(backend_utils.SKY_RAY_YAML_REMOTE_PATH))

    def _run(self):
        autostop_config = autostop_lib.get_autostop_config()

        if (autostop_config.autostop_idle_minutes < 0 or
                autostop_config.boot_time != psutil.boot_time()):
            self.last_active_time = time.time()
            logger.debug('autostop_config not set. Skipped.')
            return

        if job_lib.is_cluster_idle():
            idle_minutes = (time.time() - self.last_active_time) // 60
            logger.debug(
                f'Idle minutes: {idle_minutes}, '
                f'AutoStop config: {autostop_config.autostop_idle_minutes}')
        else:
            self.last_active_time = time.time()
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
            self._replace_yaml_for_stopping(self.ray_yaml_path)
            # `ray up` is required to reset the upscaling speed and min/max
            # workers. Otherwise, `ray down --workers-only` will continuously
            # scale down and up.
            subprocess.run(
                ['ray', 'up', '-y', '--restart-only', self.ray_yaml_path],
                check=True)
            # Stop the workers first to avoid orphan workers.
            subprocess.run(
                ['ray', 'down', '-y', '--workers-only', self.ray_yaml_path],
                check=True)
            subprocess.run(['ray', 'down', '-y', self.ray_yaml_path],
                           check=True)
        else:
            raise NotImplementedError

    def _replace_yaml_for_stopping(self, yaml_path: str):
        with open(yaml_path, 'r') as f:
            yaml_str = f.read()
        # Update the number of workers to 0.
        yaml_str = self._NUM_WORKER_PATTERN.sub(r'\g<1>_workers: 0', yaml_str)
        yaml_str = self._UPSCALING_PATTERN.sub(r'upscaling_speed: 0', yaml_str)
        yaml_str = self._CATCH_NODES.sub(r'cache_stopped_nodes: true', yaml_str)
        config = yaml.safe_load(yaml_str)
        # Set the private key with the existed key on the remote instance.
        config['auth']['ssh_private_key'] = '~/ray_bootstrap_key.pem'
        # Empty the file_mounts.
        config['file_mounts'] = dict()
        backend_utils.dump_yaml(yaml_path, config)
        logger.debug('Replaced worker num and upscaling speed to 0.')
