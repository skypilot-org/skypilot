"""Common data structures and constants used in the API."""
import enum

class StatusRefreshMode(enum.Enum):
    """The mode of refreshing the status of a cluster."""
    
    NONE = 'NONE'
    # Automatically refresh when needed, e.g., autostop is set or the cluster
    # is a spot instance.
    AUTO = 'AUTO'
    FORCE = 'FORCE'


