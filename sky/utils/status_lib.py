"""Statuses enum for SkyPilot resources."""

import enum

import colorama


class ClusterStatus(enum.Enum):
    """Cluster status as recorded in local cache.

    This can be different from the actual cluster status, and can be refreshed
    by running ``sky status --refresh``.
    """
    # NOTE: these statuses are as recorded in our local cache, the table
    # 'clusters'.  The actual cluster state may be different (e.g., an UP
    # cluster getting killed manually by the user or the cloud provider).

    INIT = 'INIT'
    """Initializing.

    This means a provisioning has started but has not successfully finished. The
    cluster may be undergoing setup, may have failed setup, may be live or down.
    """

    UP = 'UP'
    """The cluster is up. This means a provisioning has previously succeeded."""

    STOPPED = 'STOPPED'
    """The cluster is stopped."""

    def colored_str(self):
        color = _STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'


_STATUS_TO_COLOR = {
    ClusterStatus.INIT: colorama.Fore.BLUE,
    ClusterStatus.UP: colorama.Fore.GREEN,
    ClusterStatus.STOPPED: colorama.Fore.YELLOW,
}


class StorageStatus(enum.Enum):
    """Storage status as recorded in table 'storage'."""

    # Initializing and uploading storage
    INIT = 'INIT'

    # Initialization failed
    INIT_FAILED = 'INIT_FAILED'

    # Failed to Upload to Cloud
    UPLOAD_FAILED = 'UPLOAD_FAILED'

    # Finished uploading, in terminal state
    READY = 'READY'


class VolumeStatus(enum.Enum):
    """Volume status as recorded in table 'volumes'."""

    # Volume is ready to be used
    READY = 'READY'

    # Volume is being used
    IN_USE = 'IN_USE'
