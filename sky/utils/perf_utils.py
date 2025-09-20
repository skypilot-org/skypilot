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
