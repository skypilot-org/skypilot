"""skylet: a daemon running on the head node of a cluster."""

import time

import sky
from sky import sky_logging
from sky.skylet import constants
from sky.skylet import events

# Use the explicit logger name so that the logger is under the
# `sky.skylet.skylet` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.skylet.skylet')
logger.info(f'Skylet started with version {constants.SKYLET_VERSION}; '
            f'SkyPilot v{sky.__version__} (commit: {sky.__commit__})')

EVENTS = [
    events.AutostopEvent(),
    events.JobSchedulerEvent(),
    # The spot job update event should be after the job update event.
    # Otherwise, the abnormal spot job status update will be delayed
    # until the next job update event.
    events.SpotJobUpdateEvent(),
]

while True:
    time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)
    for event in EVENTS:
        event.run()
