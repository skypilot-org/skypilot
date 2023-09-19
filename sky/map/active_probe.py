"""SkyMap Acive Probe: When the number of data for region is too small,
active probe the regions to get more wait time data.
"""

import threading

import sky
from sky import sky_logging

logger = sky_logging.init_logger('sky.map.active_probe')


class ActiveProbe:
    """Actively probing different regions for SkyMap."""

    def __init__(self) -> None:
        pass

    def _active_probe_thread(self, cloud: str, region: str,
                             accelerator: str) -> None:

        task = sky.Task(run='nvidia-smi')

        if cloud == 'GCP':
            task.set_resources(
                sky.Resources(sky.GCP(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'AWS':
            task.set_resources(
                sky.Resources(sky.AWS(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'Azure':
            task.set_resources(
                sky.Resources(sky.Azure(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'IBM':
            task.set_resources(
                sky.Resources(sky.IBM(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'Lambda':
            task.set_resources(
                sky.Resources(sky.Lambda(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'OCI':
            task.set_resources(
                sky.Resources(sky.OCI(),
                              region=region,
                              accelerators=accelerator))
        elif cloud == 'SCP':
            task.set_resources(
                sky.Resources(sky.SCP(),
                              region=region,
                              accelerators=accelerator))
        else:
            logger.info(f'Cloud {cloud} is not supported.')
            return

        logger.info(f'Active probing {cloud} {region} {accelerator}')
        sky.launch(task, down=True, detach_setup=True, detach_run=True)

    def active_probe(self, cloud: str, region: str, accelerator: str) -> None:

        thr = threading.Thread(target=self._active_probe_thread,
                               args=(
                                   cloud,
                                   region,
                                   accelerator,
                               ))
        thr.start()
        logger.info(thr.is_alive())
