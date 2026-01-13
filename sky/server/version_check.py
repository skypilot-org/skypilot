"""Version check module for checking PyPI for latest SkyPilot versions."""
import asyncio
import json
import pathlib
import re
import time
from typing import Optional

from packaging import version as version_lib
import requests

import sky
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Cache file location
VERSION_CACHE_FILE = pathlib.Path.home() / '.sky' / 'version_cache.json'
# Cache TTL: 1 day in seconds
CACHE_TTL_SECONDS = 24 * 60 * 60

# Dev version pattern - skip checking for dev versions
DEV_VERSION_PATTERN = re.compile(r'^.*-dev\d*$', re.IGNORECASE)

# Version info cache
_version_cache: Optional[dict] = None


def _is_dev_version(version_str: str) -> bool:
    """Check if version is a dev version."""
    return bool(DEV_VERSION_PATTERN.match(version_str))


def _load_cache() -> Optional[dict]:
    """Load version cache from disk."""
    global _version_cache
    if _version_cache is not None:
        return _version_cache

    if not VERSION_CACHE_FILE.exists():
        return None

    try:
        with open(VERSION_CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            _version_cache = cache
            return cache
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to load version cache: {e}')
        return None


def _save_cache(cache: dict) -> None:
    """Save version cache to disk."""
    global _version_cache
    _version_cache = cache

    # Ensure directory exists
    VERSION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(VERSION_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to save version cache: {e}')


def _is_cache_valid(cache: dict) -> bool:
    """Check if cache is still valid (not expired)."""
    cached_time = cache.get('cached_at', 0)
    current_time = time.time()
    return (current_time - cached_time) < CACHE_TTL_SECONDS


def _check_pypi_release() -> Optional[str]:
    """Check pypi.org for latest release version."""
    try:
        # Check pypi.org for skypilot
        url = 'https://pypi.org/pypi/skypilot/json'
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Get the latest version from info
            latest_version = data.get('info', {}).get('version')
            if latest_version:
                return latest_version
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to check pypi.org for release: {e}')
    return None


def _check_pypi_versions() -> Optional[str]:
    """Check PyPI for latest stable release version.

    Returns:
        Latest stable release version, or None if not available.
    """
    return _check_pypi_release()


def get_latest_versions() -> Optional[str]:
    """Get latest stable release version from cache or PyPI.

    Returns:
        Latest stable release version, or None if not available.
    """
    # Check cache first
    cache = _load_cache()
    if cache and _is_cache_valid(cache):
        return cache.get('release_version')

    # Cache expired or doesn't exist, check PyPI
    release_version = _check_pypi_versions()

    # Save to cache
    _save_cache({
        'release_version': release_version,
        'cached_at': time.time(),
    })

    return release_version


def get_latest_version_for_current() -> Optional[str]:
    """Get latest stable release version for notifications.

    Returns:
        Latest stable release version (not nightly or dev). Returns None
        if current version is dev, if no latest version is available, or
        if latest version is not newer than current version.
    """
    # Skip for dev versions - cache should handle this, but double-check
    if _is_dev_version(sky.__version__):
        return None

    latest_version = get_latest_versions()

    # Only return if latest version exists and is newer than current
    if latest_version:
        # Skip if latest version is a dev/nightly version
        if _is_dev_version(latest_version) or '.dev' in latest_version.lower():
            return None
        if version_lib.parse(latest_version) > version_lib.parse(
                sky.__version__):
            return latest_version

    return None


async def check_versions_periodically():
    """Background task to check PyPI versions periodically (daily)."""
    while True:
        try:
            # Check versions (will use cache if valid,
            # otherwise fetch from PyPI)
            get_latest_versions()
            logger.debug('Version check completed')
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Error in periodic version check: {e}')

        # Sleep for 1 day (in seconds)
        await asyncio.sleep(CACHE_TTL_SECONDS)
