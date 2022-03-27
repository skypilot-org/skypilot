"""skylet events"""
import os
import psutil
import re
import subprocess
import time
import traceback
import yaml

from sky import sky_logging
from sky.backends import backend_utils
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.skylet import autostop_lib, job_lib

EVENT_CHECKING_INTERVAL = 1
logger = sky_logging.init_logger(__name__)


class SkyletEvent:
    """Skylet event.

    Usage: override the EVENT_INTERVAl and _run method in subclass.
    """
    EVENT_INTERVAL = -1

    def __init__(self):
        self.event_interval = self.EVENT_INTERVAL
        self.current_time_stamp = 0

    def step(self):
        self.current_time_stamp = (self.current_time_stamp +
                                   1) % self.event_interval
        if self.current_time_stamp % self.event_interval == 0:
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
    EVENT_INTERVAL = 20

    def _run(self):
        job_lib.update_status()


class AutostopEvent(SkyletEvent):
    """Skylet event for autostop."""
    EVENT_INTERVAL = 60

    NUM_WORKER_PATTERN = re.compile(r'((?:min|max))_workers: (\d+)')
    UPSCALING_PATTERN = re.compile(r'upscaling_speed: (\d+)')

    def __init__(self):
        super().__init__()
        self.last_active_time = time.time()
        self.ray_yaml_path = os.path.abspath(
            os.path.expanduser(backend_utils.SKY_RAY_YAML_REMOTE_PATH))

    def _run(self):
        autostop_config = autostop_lib.get_autostop_config()

        if (autostop_config.autostop_idle_minutes < 0 or
                autostop_config.boot_time != psutil.boot_time()):
            logger.debug('autostop_config not set. Skipped.')
            return

        if job_lib.is_idle():
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
            logger.info(f'idle_minutes {idle_minutes} reached config '
                        f'{autostop_config.autostop_idle_minutes}. Stopping.')
            self._stop_cluster(autostop_config)

    def _stop_cluster(self, autostop_config):
        if autostop_config.backend == CloudVmRayBackend.NAME:
            self._update_yaml(self.ray_yaml_path)
            # Destroy the workers first to avoid orphan workers.
            subprocess.run(
                ['ray', 'up', '-v', '-y', '--restart-only', self.ray_yaml_path],
                check=True)
            subprocess.run(['ray', 'down', '-y', self.ray_yaml_path],
                           check=True)

            pass
        else:
            raise NotImplementedError

    def _update_yaml(self, yaml_path: str):
        with open(yaml_path, 'r') as f:
            yaml_str = f.read()
        yaml_str = self.NUM_WORKER_PATTERN.sub(r'\g<1>_workers: 0', yaml_str)
        yaml_str = self.UPSCALING_PATTERN.sub(r'upscaling_speed: 0', yaml_str)
        config = yaml.load(yaml_str)
        config['auth']['ssh_private_key'] = '~/ray_bootstrap_key.pem'
        config['file_mounts'] = dict()
        backend_utils.dump_yaml(yaml_path, config)
        print('Replaced worker num and upscaling speed to 0.')
