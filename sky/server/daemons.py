"""Internal server daemons that run in the background."""
import dataclasses
import os
import time
from typing import Callable, Optional

from sky import sky_logging
from sky import skypilot_config
from sky.server import constants as server_constants
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def _default_should_skip():
    return False


@dataclasses.dataclass
class InternalRequestDaemon:
    """Internal daemon that runs an event in the background."""

    id: str
    name: str
    event_fn: Callable[[], None]
    interval: int
    start_fn: Optional[Callable[[], None]] = None
    default_log_level: str = 'INFO'
    should_skip: Callable[[], bool] = _default_should_skip

    def refresh_log_level(self) -> int:
        # pylint: disable=import-outside-toplevel
        import logging

        try:
            # Refresh config within the while loop.
            # Since this is a long running daemon,
            # reload_config_for_new_request()
            # is not called in between the event runs.
            skypilot_config.safe_reload_config()
            # Get the configured log level for the daemon inside the event loop
            # in case the log level changes after the API server is started.
            level_str = skypilot_config.get_nested(
                ('daemons', self.id, 'log_level'), self.default_log_level)
            return getattr(logging, level_str.upper())
        except AttributeError:
            # Bad level should be rejected by
            # schema validation, just in case.
            logger.warning(f'Invalid log level: {level_str}, using DEBUG')
            return logging.DEBUG
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(f'Error refreshing log level for {self.id}: {e}')
            return logging.DEBUG

    def _run_fn(self, fn: Callable[[], None], level: int) -> int:
        """Run the function. Returns the new log level."""
        with ux_utils.enable_traceback(), \
            sky_logging.set_sky_logging_levels(level):
            sky_logging.reload_logger()
            level = self.refresh_log_level()
            fn()

        # Clear request level cache after each run to avoid
        # using too much memory.
        annotations.clear_request_level_cache()
        timeline.save_timeline()
        # Kill all children processes related to this request.
        # Each executor handles a single request, so we can safely
        # kill all children processes related to this request.
        subprocess_utils.kill_children_processes()
        common_utils.release_memory()

        return level

    def run_event(self):
        """Run the event."""

        # Disable logging for periodic refresh to avoid the usage message being
        # sent multiple times.
        os.environ[env_options.Options.DISABLE_LOGGING.env_key] = '1'

        level = self.refresh_log_level()
        if self.start_fn is not None:
            level = self._run_fn(self.start_fn, level)

        while True:
            try:
                level = self._run_fn(self.event_fn, level)
                logger.info(f'Refreshed. Sleeping {self.interval} seconds for '
                            'the next refresh...')
                time.sleep(self.interval)
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


def refresh_volume_status_event():
    """Periodically refresh the volume status."""
    # pylint: disable=import-outside-toplevel
    from sky.volumes.server import core

    # Disable logging for periodic refresh to avoid the usage message being
    # sent multiple times.
    os.environ[env_options.Options.DISABLE_LOGGING.env_key] = '1'

    logger.info('=== Refreshing volume status ===')
    core.volume_refresh()


def managed_job_status_refresh_event():
    """Refresh the managed job status for controller consolidation mode."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs import scheduler
    from sky.jobs import utils as managed_job_utils

    # Make sure the controllers are running.
    scheduler.maybe_start_controllers()

    logger.info('=== Running managed job event ===')
    managed_job_utils.update_managed_jobs_statuses()


def managed_job_status_start_event():
    """Start the managed job status refresh event."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs import scheduler

    # On start, we should make sure all the controllers are fresh. If there are
    # existing controllers lingering from a manually terminated API server, they
    # should be stopped.
    scheduler.maybe_start_controllers(stop_existing_controllers=True)


def should_skip_managed_job_status_refresh():
    """Check if the managed job status refresh event should be skipped."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs import utils as managed_job_utils
    return not managed_job_utils.is_consolidation_mode()


def _serve_status_refresh_event(pool: bool):
    """Refresh the sky serve status for controller consolidation mode."""
    # pylint: disable=import-outside-toplevel
    from sky.serve import serve_utils

    # We run the recovery logic before starting the event loop as those two are
    # conflicting. Check PERSISTENT_RUN_RESTARTING_SIGNAL_FILE for details.
    serve_utils.ha_recovery_for_consolidation_mode(pool=pool)

    # After recovery, we start the event loop.
    from sky.skylet import events
    event = events.ServiceUpdateEvent(pool=pool)
    noun = 'pool' if pool else 'serve'
    logger.info(f'=== Running {noun} status refresh event ===')
    event.run()


def _should_skip_serve_status_refresh_event(pool: bool):
    """Check if the serve status refresh event should be skipped."""
    # pylint: disable=import-outside-toplevel
    from sky.serve import serve_utils
    return not serve_utils.is_consolidation_mode(pool=pool)


def sky_serve_status_refresh_event():
    _serve_status_refresh_event(pool=False)


def should_skip_sky_serve_status_refresh():
    return _should_skip_serve_status_refresh_event(pool=False)


def pool_status_refresh_event():
    _serve_status_refresh_event(pool=True)


def should_skip_pool_status_refresh():
    return _should_skip_serve_status_refresh_event(pool=True)


# Register the events to run in the background.
INTERNAL_REQUEST_DAEMONS = [
    # This status refresh daemon can cause the autostopp'ed/autodown'ed cluster
    # set to updated status automatically, without showing users the hint of
    # cluster being stopped or down when `sky status -r` is called.
    InternalRequestDaemon(
        id='skypilot-status-refresh-daemon',
        name='status-refresh',
        event_fn=refresh_cluster_status_event,
        interval=server_constants.CLUSTER_REFRESH_DAEMON_INTERVAL_SECONDS,
        default_log_level='DEBUG'),
    # Volume status refresh daemon to update the volume status periodically.
    InternalRequestDaemon(
        id='skypilot-volume-status-refresh-daemon',
        name='volume-refresh',
        event_fn=refresh_volume_status_event,
        interval=server_constants.VOLUME_REFRESH_DAEMON_INTERVAL_SECONDS),
    InternalRequestDaemon(
        id='managed-job-status-refresh-daemon',
        name='managed-job-status-refresh',
        event_fn=managed_job_status_refresh_event,
        start_fn=managed_job_status_start_event,
        interval=server_constants.MANAGED_JOB_REFRESH_DAEMON_INTERVAL_SECONDS,
        should_skip=should_skip_managed_job_status_refresh),
    InternalRequestDaemon(
        id='sky-serve-status-refresh-daemon',
        name='sky-serve-status-refresh',
        event_fn=sky_serve_status_refresh_event,
        interval=server_constants.SKY_SERVE_REFRESH_DAEMON_INTERVAL_SECONDS,
        should_skip=should_skip_sky_serve_status_refresh),
    InternalRequestDaemon(
        id='pool-status-refresh-daemon',
        name='pool-status-refresh',
        event_fn=pool_status_refresh_event,
        interval=server_constants.SKY_SERVE_REFRESH_DAEMON_INTERVAL_SECONDS,
        should_skip=should_skip_pool_status_refresh),
]


def is_daemon_request_id(request_id: str) -> bool:
    """Returns whether a specific request_id is an internal daemon."""
    return any([d.id == request_id for d in INTERNAL_REQUEST_DAEMONS])
