"""Internal server daemons that run in the background."""
import dataclasses
import os
import time
from typing import Callable

from sky import sky_logging
from sky import skypilot_config
from sky.server import constants as server_constants
from sky.utils import common
from sky.utils import env_options
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass
class InternalRequestDaemon:
    """Internal daemon that runs an event in the background."""

    id: str
    name: str
    event_fn: Callable[[], None]
    default_log_level: str = 'INFO'

    def run_event(self):
        """Run the event."""
        # pylint: disable=import-outside-toplevel
        import logging

        # Disable logging for periodic refresh to avoid the usage message being
        # sent multiple times.
        os.environ[env_options.Options.DISABLE_LOGGING.env_key] = '1'

        while True:
            # Refresh config within the while loop.
            # Since this is a long running daemon,
            # reload_config_for_new_request()
            # is not called in between the event runs.
            skypilot_config.safe_reload_config()
            # Get the configured log level for the daemon inside the event loop
            # in case the log level changes after the API server is started.
            level_str = skypilot_config.get_nested(
                ('daemons', self.id, 'log_level'), self.default_log_level)
            try:
                level = getattr(logging, level_str.upper())
            except AttributeError:
                # Bad level should be rejected by
                # schema validation, just in case.
                logger.warning(f'Invalid log level: {level_str}, using DEBUG')
                level = logging.DEBUG
            with ux_utils.enable_traceback(), \
                sky_logging.set_sky_logging_levels(level):
                sky_logging.reload_logger()
                try:
                    self.event_fn()
                except Exception:  # pylint: disable=broad-except
                    # It is OK to fail to run the event, as the event is not
                    # critical, but we should log the error.
                    logger.exception(
                        f'Error running {self.name} event. '
                        f'Restarting in '
                        f'{server_constants.DAEMON_RESTART_INTERVAL_SECONDS} '
                        'seconds...')
                    time.sleep(server_constants.DAEMON_RESTART_INTERVAL_SECONDS)


def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    # pylint: disable=import-outside-toplevel
    from sky import core

    logger.info('=== Refreshing cluster status ===')
    # This periodically refresh will hold the lock for the cluster being
    # refreshed, but it is OK because other operations will just wait for
    # the lock and get the just refreshed status without refreshing again.
    core.status(refresh=common.StatusRefreshMode.FORCE, all_users=True)
    logger.info('Status refreshed. Sleeping '
                f'{server_constants.CLUSTER_REFRESH_DAEMON_INTERVAL_SECONDS}'
                ' seconds for the next refresh...\n')
    time.sleep(server_constants.CLUSTER_REFRESH_DAEMON_INTERVAL_SECONDS)


def refresh_volume_status_event():
    """Periodically refresh the volume status."""
    # pylint: disable=import-outside-toplevel
    from sky.volumes.server import core

    # Disable logging for periodic refresh to avoid the usage message being
    # sent multiple times.
    os.environ[env_options.Options.DISABLE_LOGGING.env_key] = '1'

    logger.info('=== Refreshing volume status ===')
    core.volume_refresh()
    logger.info('Volume status refreshed. Sleeping '
                f'{server_constants.VOLUME_REFRESH_DAEMON_INTERVAL_SECONDS}'
                ' seconds for the next refresh...\n')
    time.sleep(server_constants.VOLUME_REFRESH_DAEMON_INTERVAL_SECONDS)


def managed_job_status_refresh_event():
    """Refresh the managed job status for controller consolidation mode."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs import utils as managed_job_utils
    if not managed_job_utils.is_consolidation_mode():
        return
    # We run the recovery logic before starting the event loop as those two are
    # conflicting. Check PERSISTENT_RUN_RESTARTING_SIGNAL_FILE for details.
    from sky.utils import controller_utils
    if controller_utils.high_availability_specified(
            controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name):
        managed_job_utils.ha_recovery_for_consolidation_mode()
    # After recovery, we start the event loop.
    from sky.skylet import events
    refresh_event = events.ManagedJobEvent()
    scheduling_event = events.ManagedJobSchedulingEvent()
    logger.info('=== Running managed job event ===')
    refresh_event.run()
    scheduling_event.run()
    time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)


def sky_serve_status_refresh_event():
    """Refresh the sky serve status for controller consolidation mode."""
    # pylint: disable=import-outside-toplevel
    from sky.serve import serve_utils
    if not serve_utils.is_consolidation_mode():
        return
    # TODO(tian): Add HA recovery logic.
    from sky.skylet import events
    event = events.ServiceUpdateEvent()
    logger.info('=== Running serve status refresh event ===')
    event.run()
    time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)


# Register the events to run in the background.
INTERNAL_REQUEST_DAEMONS = [
    # This status refresh daemon can cause the autostopp'ed/autodown'ed cluster
    # set to updated status automatically, without showing users the hint of
    # cluster being stopped or down when `sky status -r` is called.
    InternalRequestDaemon(id='skypilot-status-refresh-daemon',
                          name='status',
                          event_fn=refresh_cluster_status_event,
                          default_log_level='DEBUG'),
    # Volume status refresh daemon to update the volume status periodically.
    InternalRequestDaemon(id='skypilot-volume-status-refresh-daemon',
                          name='volume',
                          event_fn=refresh_volume_status_event),
    InternalRequestDaemon(id='managed-job-status-refresh-daemon',
                          name='managed-job-status',
                          event_fn=managed_job_status_refresh_event),
    InternalRequestDaemon(id='sky-serve-status-refresh-daemon',
                          name='sky-serve-status',
                          event_fn=sky_serve_status_refresh_event),
]
