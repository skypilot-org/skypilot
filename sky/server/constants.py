"""Constants for the API servers."""

from sky.skylet import constants

# API server version, whenever there is a change in API server that requires a
# restart of the local API server or error out when the client does not match
# the server version.
API_VERSION = '3'

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

# The interval (seconds) for the cluster status to be refreshed in the
# background.
CLUSTER_REFRESH_DAEMON_INTERVAL_SECONDS = 60

# Environment variable for a file path to the API cookie file.
API_COOKIE_FILE_ENV_VAR = f'{constants.SKYPILOT_ENV_VAR_PREFIX}API_COOKIE_FILE'
