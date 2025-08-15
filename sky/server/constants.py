"""Constants for the API servers."""

import os

from sky.skylet import constants

# pylint: disable=line-too-long
# The SkyPilot API version that the code currently use.
# Bump this version when the API is changed and special compatibility handling
# based on version info is needed.
# For more details and code guidelines, refer to:
# https://docs.skypilot.co/en/latest/developers/CONTRIBUTING.html#backward-compatibility-guidelines
API_VERSION = 17

# The minimum peer API version that the code should still work with.
# Notes (dev):
# - This value is maintained by the CI pipeline, DO NOT EDIT this manually.
# - Compatibility code for versions lower than this can be safely removed.
# Refer to API_VERSION for more details.
MIN_COMPATIBLE_API_VERSION = 11

# The semantic version of the minimum compatible API version.
# Refer to MIN_COMPATIBLE_API_VERSION for more details.
# Note (dev): DO NOT EDIT this constant manually.
MIN_COMPATIBLE_VERSION = '0.10.0'

# The HTTP header name for the API version of the sender.
API_VERSION_HEADER = 'X-SkyPilot-API-Version'

# The HTTP header name for the SkyPilot version of the sender.
VERSION_HEADER = 'X-SkyPilot-Version'

# Prefix for API request names.
REQUEST_NAME_PREFIX = 'sky.'
# The memory (GB) that SkyPilot tries to not use to prevent OOM.
MIN_AVAIL_MEM_GB = 2
# Default encoder/decoder handler name.
DEFAULT_HANDLER_NAME = 'default'
# The path to the API request database.
API_SERVER_REQUEST_DB_PATH = '~/.sky/api_server/requests.db'

# The interval (seconds) for the cluster status to be refreshed in the
# background.
CLUSTER_REFRESH_DAEMON_INTERVAL_SECONDS = 60

# The interval (seconds) for the volume status to be refreshed in the
# background.
VOLUME_REFRESH_DAEMON_INTERVAL_SECONDS = 60

# Environment variable for a file path to the API cookie file.
# Keep in sync with websocket_proxy.py
API_COOKIE_FILE_ENV_VAR = f'{constants.SKYPILOT_ENV_VAR_PREFIX}API_COOKIE_FILE'
# Default file if unset.
# Keep in sync with websocket_proxy.py
API_COOKIE_FILE_DEFAULT_LOCATION = '~/.sky/cookies.txt'

# The path to the dashboard build output
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), '..', 'dashboard',
                             'out')

# The interval (seconds) for the event to be restarted in the background.
DAEMON_RESTART_INTERVAL_SECONDS = 20
