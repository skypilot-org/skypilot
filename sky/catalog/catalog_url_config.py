"""Configuration for custom catalog URLs.

This module provides a centralized way to configure custom catalog URLs
for different cloud providers. URLs can be overridden via environment variables
to use custom/private catalog repositories instead of the default public ones.

Environment Variables:
    SKYPILOT_CATALOG_BASE_URL: Override base URL for all catalogs
    SKYPILOT_<CLOUD>_CATALOG_URL: Override URL for specific cloud provider
        Examples:
        - SKYPILOT_RUNPOD_CATALOG_URL
        - SKYPILOT_VAST_CATALOG_URL
        - SKYPILOT_AWS_CATALOG_URL
        - SKYPILOT_GCP_CATALOG_URL
        - SKYPILOT_AZURE_CATALOG_URL

Example Usage:
    # Use custom catalog for RunPod only
    export SKYPILOT_RUNPOD_CATALOG_URL="https://raw.githubusercontent.com/\
php-workx/skypilot-catalog/main/catalogs/v8/runpod/vms.csv"

    # Use custom base URL for all catalogs
    export SKYPILOT_CATALOG_BASE_URL="https://raw.githubusercontent.com/\
php-workx/skypilot-catalog/main/catalogs"
"""

import os
from typing import Optional

from sky.skylet import constants


def get_catalog_url(filename: str) -> str:
    """Get the catalog URL for a given filename.

    Checks for custom URLs in the following order:
    1. Cloud-specific environment variable (SKYPILOT_<CLOUD>_CATALOG_URL)
    2. Base URL override (SKYPILOT_CATALOG_BASE_URL)
    3. Default SkyPilot hosted catalog URL

    Args:
        filename: Relative path to catalog file (e.g., 'runpod/vms.csv')

    Returns:
        Full URL to the catalog file

    Examples:
        >>> # With default URLs
        >>> get_catalog_url('runpod/vms.csv')
        'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/\
master/catalogs/v8/runpod/vms.csv'

        >>> # With custom RunPod URL
        >>> os.environ['SKYPILOT_RUNPOD_CATALOG_URL'] = \
'https://example.com/runpod/vms.csv'
        >>> get_catalog_url('runpod/vms.csv')
        'https://example.com/runpod/vms.csv'

        >>> # With custom base URL
        >>> os.environ['SKYPILOT_CATALOG_BASE_URL'] = \
'https://example.com/catalogs'
        >>> get_catalog_url('aws/vms.csv')
        'https://example.com/catalogs/v8/aws/vms.csv'
    """
    # Extract cloud name from filename (e.g., 'runpod' from 'runpod/vms.csv')
    cloud_name = os.path.dirname(filename).split('/')[0].upper()

    # Check for cloud-specific URL override
    cloud_specific_env = f'SKYPILOT_{cloud_name}_CATALOG_URL'
    custom_url = os.getenv(cloud_specific_env)

    if custom_url:
        return custom_url

    # Check for base URL override
    base_url = os.getenv('SKYPILOT_CATALOG_BASE_URL')
    if base_url:
        # Remove trailing slash from base URL
        base_url = base_url.rstrip('/')
        return f'{base_url}/{constants.CATALOG_SCHEMA_VERSION}/{filename}'

    # Use default hosted catalog URL
    return (f'{constants.HOSTED_CATALOG_DIR_URL}/'
            f'{constants.CATALOG_SCHEMA_VERSION}/{filename}')


def get_catalog_url_fallback(filename: str) -> Optional[str]:
    """Get the fallback catalog URL for a given filename.

    Args:
        filename: Relative path to catalog file (e.g., 'runpod/vms.csv')

    Returns:
        Returns S3 mirror URL
    """
    # Use default S3 mirror as fallback
    return (f'{constants.HOSTED_CATALOG_DIR_URL_S3_MIRROR}/'
            f'{constants.CATALOG_SCHEMA_VERSION}/{filename}')
