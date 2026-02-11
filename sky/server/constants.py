"""Constants for the API servers."""

import os

from sky.skylet import constants

# pylint: disable=line-too-long
# The SkyPilot API version that the code currently use.
# Bump this version when the API is changed and special compatibility handling
# based on version info is needed.
# For more details and code guidelines, refer to:
# https://docs.skypilot.co/en/latest/developers/CONTRIBUTING.html#backward-compatibility-guidelines
API_VERSION = 37  # Add mount cache features

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

# Minimum client API version required to launch recipes.
MIN_RECIPE_LAUNCH_API_VERSION = 33

# Prefix for API request names.
REQUEST_NAME_PREFIX = 'sky.'
# The memory (GB) that SkyPilot tries to not use to prevent OOM.
MIN_AVAIL_MEM_GB = 2
MIN_AVAIL_MEM_GB_CONSOLIDATION_MODE = 4
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

# Timeout for CLI authentication sessions (polling-based auth flow).
# Used by both client (polling timeout) and server (session expiration).
AUTH_SESSION_TIMEOUT_SECONDS = 300  # 5 minutes

# Cookie header for stream request id.
STREAM_REQUEST_HEADER = 'X-SkyPilot-Stream-Request-ID'

# Valid empty values for pickled fields (base64-encoded pickled None)
# base64.b64encode(pickle.dumps(None)).decode('utf-8')
EMPTY_PICKLED_VALUE = 'gAROLg=='

# We do not support setting these in config.yaml because:
# 1. config.yaml can be updated dynamically, but auth middleware does not
#    support hot reload yet.
# 2. If we introduce hot reload for auth middleware, bad config might
#    invalidate all authenticated sessions and thus cannot be rolled back
#    by API users.
# TODO(aylei): we should introduce server.yaml for static server admin config,
# which is more structured than multiple environment variables and can be less
# confusing to users.
OAUTH2_PROXY_BASE_URL_ENV_VAR = 'SKYPILOT_AUTH_OAUTH2_PROXY_BASE_URL'
OAUTH2_PROXY_ENABLED_ENV_VAR = 'SKYPILOT_AUTH_OAUTH2_PROXY_ENABLED'
