"""Constants for the API servers."""

import os

from sky.skylet import constants

# SkyPilot API version that the code currently use. This version should be
# bumped when:
# - there is a change in API that breaks the compatbility;
# - or special compatbility handling based on the version info is needed.
API_VERSION = 11

# The minimum remote API version that the code can still work with. This
# is usually set to the API version of the lowest SkyPilot pypi version that
# current code guarantees to be compatible with.
# Note (dev): This field is typically set by the CI pipeline based on our
# versioning strategy and should not be updated manually.
MIN_COMPATIBLE_API_VERSION = 11

# The minimum SkyPilot release version that the code can still work with.
# This provides a user-friendly message of MIN_COMPATIBLE_API_VERSION, and
# should be updated when MIN_COMPATIBLE_API_VERSION is updated. Refer to
# MIN_COMPATIBLE_API_VERSION for more details.
MIN_COMPATIBLE_VERSION = '0.11.0'

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
