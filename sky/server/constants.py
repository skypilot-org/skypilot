"""Constants for the API servers."""

# Prefix for API request names.
REQUEST_NAME_PREFIX = 'sky.'
# The user ID of the SkyPilot system.
SKYPILOT_SYSTEM_USER_ID = 'skypilot-system'
# The memory (GB) that SkyPilot tries to not use to prevent OOM.
MIN_AVAIL_MEM_GB = 2
# Default encoder/decoder handler name.
DEFAULT_HANDLER_NAME = 'default'
# The path to the API request database.
API_SERVER_REQUEST_DB_PATH = '~/.sky/api_server/requests.db'
