"""Global environment options for sky."""
import enum
import os


class Options(enum.Enum):

    IS_DEVELOPER = 'SKYPILOT_DEV'
    SHOW_DEBUG_INFO = 'SKYPILOT_DEBUG'
    DISABLE_LOGGING = 'SKYPILOT_DISABLE_USAGE_COLLECTION'
    MINIMIZE_LOGGING = 'SKYPILOT_MINIMIZE_LOGGING'

    def get(self):
        """Check if an environment variable is set to True."""
        return os.getenv(self.value, 'False').lower() in ('true', '1')
