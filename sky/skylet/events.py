from sky import sky_logging
from sky.skylet import job_lib

EVENT_CHECKING_INTERVAL = 1
logger = sky_logging.init_logger(__name__)

class SkyletEvent:
    EVENT_INTERVAL = 1
    def __init__(self):
        self.event_interval = self.EVENT_INTERVAL
        self.current_time_stamp = 0

    def step(self):
        self.current_time_stamp = (self.current_time_stamp + 1) % self.event_interval
        if self.current_time_stamp % self.event_interval == 0:
            logger.info(f'{self.__class__.__name__} triggered')
            self._run()

    def _run(self):
        raise NotImplementedError

class JobUpdateEvent(SkyletEvent):
    EVENT_INTERVAL = 20
    def _run(self):
        job_lib.update_status()

class AutoStopEvent(SkyletEvent):
    EVENT_INTERVAL = 60
    def _run(self):
        pass
