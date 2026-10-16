"""Utility functions for performance monitoring."""
import os
from typing import Optional

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)


def get_loop_lag_threshold() -> Optional[float]:
    """Get the loop lag threshold from the environment variable."""
    lag_threshold = os.getenv(constants.ENV_VAR_LOOP_LAG_THRESHOLD_MS, None)
    if lag_threshold is not None:
        try:
            return float(lag_threshold) / 1000.0
        except ValueError:
            logger.warning(
                f'Invalid value for {constants.ENV_VAR_LOOP_LAG_THRESHOLD_MS}:'
                f' {lag_threshold}')
            return None
    return None


def get_loop_stall_threshold() -> Optional[float]:
    """Returns the event loop stall attribution threshold, in seconds.

    Defaults to DEFAULT_LOOP_STALL_THRESHOLD_MS rather than to off: the
    watchdog it gates only does work while the loop is already stalled, so
    leaving it on means attribution is available for the first incident rather
    than only for the second one. Returns None when disabled, which is either a
    non-positive value or an unparseable one.
    """
    raw = os.getenv(constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS)
    if raw is None:
        threshold_ms = constants.DEFAULT_LOOP_STALL_THRESHOLD_MS
    else:
        try:
            threshold_ms = float(raw)
        except ValueError:
            logger.warning(
                f'Invalid value for '
                f'{constants.ENV_VAR_LOOP_STALL_THRESHOLD_MS}: {raw}. Event '
                'loop stall attribution is disabled.')
            return None
    if threshold_ms <= 0:
        return None
    return threshold_ms / 1000.0
