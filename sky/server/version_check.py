"""Version check module for checking PyPI for latest SkyPilot versions."""
import asyncio
import json
import pathlib
import re
import time
from typing import Optional, Tuple

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


def _check_pypi_nightly() -> Optional[str]:
    """Check pypi.org for latest nightly version."""
    try:
        # Check pypi.org for skypilot-nightly
        url = 'https://pypi.org/pypi/skypilot-nightly/json'
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            versions = list(data.get('releases', {}).keys())
            if versions:
                # Filter dev versions and get the latest
                dev_versions = [v for v in versions if '.dev' in v]
                if dev_versions:
                    # Sort by version and return latest
                    dev_versions.sort(key=version_lib.parse)
                    return dev_versions[-1]
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to check pypi.org for nightly: {e}')
    return None


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


def _check_pypi_versions() -> Tuple[Optional[str], Optional[str]]:
    """Check PyPI for latest nightly and release versions.

    Returns:
        Tuple of (latest_nightly_version, latest_release_version)
    """
    nightly_version = _check_pypi_nightly()
    release_version = _check_pypi_release()
    return nightly_version, release_version


def get_latest_versions() -> Tuple[Optional[str], Optional[str]]:
    """Get latest versions from cache or PyPI.

    Returns:
        Tuple of (latest_nightly_version, latest_release_version)
    """
    # Check cache first
    cache = _load_cache()
    if cache and _is_cache_valid(cache):
        return cache.get('nightly_version'), cache.get('release_version')

    # Cache expired or doesn't exist, check PyPI
    nightly_version, release_version = _check_pypi_versions()

    # Save to cache
    _save_cache({
        'nightly_version': nightly_version,
        'release_version': release_version,
        'cached_at': time.time(),
    })

    return nightly_version, release_version


def get_latest_version_for_current() -> Optional[str]:
    """Get latest version for the current SkyPilot version type.

    Returns:
        Latest nightly version if current is nightly, latest release
        version otherwise. Returns None if current version is dev or if
        no latest version is available.
    """
    # Skip for dev versions - cache should handle this, but double-check
    if _is_dev_version(sky.__version__):
        return None

    nightly_version, release_version = get_latest_versions()

    # If current version is nightly, return nightly_version;
    # otherwise return release_version
    is_current_nightly = '.dev' in sky.__version__.lower()
    if is_current_nightly:
        return nightly_version
    else:
        return release_version


def compare_versions(current_version: str,
                     latest_version: Optional[str]) -> bool:
    """Compare current version with latest version.

    Args:
        current_version: Current version string
        latest_version: Latest version string from PyPI

    Returns:
        True if latest_version is newer than current_version, False otherwise
    """
    if latest_version is None:
        return False

    try:
        # Normalize versions for comparison
        # Remove 'v' prefix if present
        current = current_version.lstrip('v')
        latest = latest_version.lstrip('v')

        current_parsed = version_lib.parse(current)
        latest_parsed = version_lib.parse(latest)

        return latest_parsed > current_parsed
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to compare versions {current_version} vs '
                     f'{latest_version}: {e}')
        return False


def get_upgrade_hint(current_version: str) -> Optional[dict]:
    """Get upgrade hint if a newer version is available.

    Args:
        current_version: Current SkyPilot version

    Returns:
        Dict with 'latest_version', 'is_nightly', and 'upgrade_command'
        if upgrade available, None otherwise
    """
    # Skip checking for dev versions
    if _is_dev_version(current_version):
        return None

    nightly_version, release_version = get_latest_versions()

    # Determine which version to compare against
    # If current version is a nightly (contains .dev), compare with nightly
    # Otherwise, compare with release
    is_current_nightly = '.dev' in current_version.lower()

    if is_current_nightly:
        latest_version = nightly_version
        is_nightly = True
        upgrade_command = 'pip install --upgrade --pre skypilot-nightly'
    else:
        latest_version = release_version
        is_nightly = False
        upgrade_command = 'pip install --upgrade skypilot'

    if latest_version and compare_versions(current_version, latest_version):
        return {
            'latest_version': latest_version,
            'is_nightly': is_nightly,
            'upgrade_command': upgrade_command,
        }

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
