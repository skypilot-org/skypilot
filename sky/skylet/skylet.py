"""skylet: a daemon running on the head node of a cluster."""

import time

from sky import sky_logging
from sky.skylet import events

logger = sky_logging.init_logger(__name__)
logger.info('skylet started')

EVENTS = [
    events.JobUpdateEvent(),
    events.AutoStopEvent(),
]

while True:
    time.sleep(events.EVENT_CHECKING_INTERVAL)
    for event in EVENTS:
        event.step()
