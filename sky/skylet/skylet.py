"""skylet: a daemon running on the head node of a cluster."""

import time

from sky import sky_logging
from sky.skylet import events

logger = sky_logging.init_logger(__name__)
logger.info('skylet started')

EVENTS = [
    events.AutostopEvent(),
    events.JobUpdateEvent(),
    # The spot job update event should be after the job update event.
    # Otherwise, the abnormal spot job status update will be delayed
    # until the next job update event.
    events.SpotJobUpdateEvent(),
]

while True:
    time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)
    for event in EVENTS:
        event.run()
