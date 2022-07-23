"""Global environment options for sky."""
import enum
import os


class Options(enum.Enum):
    IS_DEVELOPER = 'SKY_DEV'
    SHOW_DEBUG_INFO = 'SKY_DEBUG'
    DISABLE_LOGGING = 'SKY_DISABLE_USAGE_COLLECTION'
    MINIMIZE_LOGGING = 'SKY_MINIMIZE_LOGGING'

    def get(self):
        """Check if an environment variable is set to True."""
        return os.getenv(self.value, 'False').lower() in ('true', '1')
