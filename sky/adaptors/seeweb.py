"""Seeweb cloud adaptor - versione ultra-semplificata."""
import configparser
from pathlib import Path

from sky.adaptors import common

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
def check_compute_credentials():
    """Checks if the user has access credentials to Seeweb's compute service."""
    try:
        # Read API key from standard Seeweb configuration file
        parser = configparser.ConfigParser()
        parser.read(Path('~/.seeweb_cloud/seeweb_keys').expanduser())
        api_key = parser['DEFAULT']['api_key'].strip()

        # Test connection by fetching servers list
        # This validates that the API key is working
        seeweb_client = ecsapi.Api(token=api_key)
        seeweb_client.fetch_servers()
        return True, None

    except FileNotFoundError:
        return False, 'Missing Seeweb API key file ~/.seeweb_cloud/seeweb_keys'
    except KeyError:
        return False, 'Missing api_key in ~/.seeweb_cloud/seeweb_keys'
    except Exception as e:  # pylint: disable=broad-except
        return False, f'Unable to authenticate with Seeweb API: {e}'


@common.load_lazy_modules(_LAZY_MODULES)
def check_storage_credentials():
    """Checks if the user has access credentials to Seeweb's storage service."""
    return check_compute_credentials()


@common.load_lazy_modules(_LAZY_MODULES)
def client():
    """Returns an authenticated ecsapi.Api object."""
    # Create authenticated client using the same credential pattern
    parser = configparser.ConfigParser()
    parser.read(Path('~/.seeweb_cloud/seeweb_keys').expanduser())
    api_key = parser['DEFAULT']['api_key'].strip()

    return ecsapi.Api(token=api_key)
