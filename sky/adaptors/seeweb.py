""" Seeweb Adaptor """
import configparser
from pathlib import Path

from sky.adaptors import common
from sky.utils import annotations


class SeewebError(Exception):
    """Base exception for Seeweb adaptor errors."""


class SeewebCredentialsFileNotFound(SeewebError):
    """Raised when the Seeweb credentials file is missing."""


class SeewebApiKeyMissing(SeewebError):
    """Raised when the Seeweb API key is missing or empty."""


class SeewebAuthenticationError(SeewebError):
    """Raised when authenticating with Seeweb API fails."""


_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Seeweb.'
                         'Try pip install "skypilot[seeweb]"')

ecsapi = common.LazyImport(
    'ecsapi',
    import_error_message=_IMPORT_ERROR_MESSAGE,
)
boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (ecsapi, boto3, botocore)


@common.load_lazy_modules(_LAZY_MODULES)
def check_compute_credentials() -> bool:
    """Checks if the user has access credentials to Seeweb's compute service.

    Returns True if credentials are valid; otherwise raises a SeewebError.
    """
    # Read API key from standard Seeweb configuration file
    key_path = Path('~/.seeweb_cloud/seeweb_keys').expanduser()
    if not key_path.exists():
        raise SeewebCredentialsFileNotFound(
            'Missing Seeweb API key file ~/.seeweb_cloud/seeweb_keys')

    parser = configparser.ConfigParser()
    parser.read(key_path)
    try:
        api_key = parser['DEFAULT']['api_key'].strip()
    except KeyError as e:
        raise SeewebApiKeyMissing(
            'Missing api_key in ~/.seeweb_cloud/seeweb_keys') from e
    if not api_key:
        raise SeewebApiKeyMissing(
            'Empty api_key in ~/.seeweb_cloud/seeweb_keys')

    # Test connection by fetching servers list to validate the key
    try:
        seeweb_client = ecsapi.Api(token=api_key)
        seeweb_client.fetch_servers()
    except Exception as e:  # pylint: disable=broad-except
        raise SeewebAuthenticationError(
            f'Unable to authenticate with Seeweb API: {e}') from e

    return True


@common.load_lazy_modules(_LAZY_MODULES)
def check_storage_credentials() -> bool:
    """Checks if the user has access credentials to Seeweb's storage service.

    Mirrors compute credentials validation.
    """
    return check_compute_credentials()


@common.load_lazy_modules(_LAZY_MODULES)
@annotations.lru_cache(scope='global', maxsize=1)
def client():
    """Returns an authenticated ecsapi.Api object."""
    # Create authenticated client using the same credential pattern
    key_path = Path('~/.seeweb_cloud/seeweb_keys').expanduser()
    if not key_path.exists():
        raise SeewebCredentialsFileNotFound(
            'Missing Seeweb API key file ~/.seeweb_cloud/seeweb_keys')

    parser = configparser.ConfigParser()
    parser.read(key_path)
    try:
        api_key = parser['DEFAULT']['api_key'].strip()
    except KeyError as e:
        raise SeewebApiKeyMissing(
            'Missing api_key in ~/.seeweb_cloud/seeweb_keys') from e
    if not api_key:
        raise SeewebApiKeyMissing(
            'Empty api_key in ~/.seeweb_cloud/seeweb_keys')

    return ecsapi.Api(token=api_key)
