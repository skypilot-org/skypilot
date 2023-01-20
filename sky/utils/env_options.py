"""Global environment options for sky."""
import enum
import os


class Options(enum.Enum):
    """Environment variables for SkyPilot."""
    IS_DEVELOPER = 'SKYPILOT_DEV'
    SHOW_DEBUG_INFO = 'SKYPILOT_DEBUG'
    DISABLE_LOGGING = 'SKYPILOT_DISABLE_USAGE_COLLECTION'
    MINIMIZE_LOGGING = 'SKYPILOT_MINIMIZE_LOGGING'
    # Internal: this is used to skip the cloud user identity check,
    # which is used to protect cluster operations in a multi-identity
    # scenario. Currently, this is only used in the spot controller,
    # as there will not be multiple identities, and skipping the check
    # can increase robustness.
    SKIP_CLOUD_IDENTITY_CHECK = 'SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK'

    def get(self):
        """Check if an environment variable is set to True."""
        return os.getenv(self.value, 'False').lower() in ('true', '1')
