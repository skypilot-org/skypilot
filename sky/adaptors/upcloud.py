"""UpCloud adaptor."""
import os
import threading
from typing import Optional

import requests

from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils
from sky.utils import yaml_utils

# upctl's configuration file; also read by the UpCloud Terraform provider docs.
UPCTL_CONFIG_PATH = '~/.config/upctl.yaml'
UPCLOUD_PROFILE_NAME = 'upcloud'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for UpCloud. '
                         'Try pip install "skypilot[upcloud]".')
_STORAGE_IMPORT_ERROR_MESSAGE = (
    'Failed to import dependencies for UpCloud Managed Object Storage. '
    'Try pip install "skypilot[upcloud]" or pip install boto3.')

boto3 = common.LazyImport('boto3',
                          import_error_message=_STORAGE_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_STORAGE_IMPORT_ERROR_MESSAGE)
_STORAGE_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()
API_BASE = 'https://api.upcloud.com/1.3'


class UpCloudError(Exception):
    """Base exception for UpCloud adaptor errors."""


class UpCloudCredentialsNotFound(UpCloudError):
    """Raised when UpCloud credentials are missing."""


def get_api_token() -> str:
    """Returns the UpCloud API token from upctl's configuration file."""
    config_path = os.path.expanduser(UPCTL_CONFIG_PATH)
    if not os.path.exists(config_path):
        raise UpCloudCredentialsNotFound(
            f'Missing UpCloud credentials. Add '
            f'"token: <api-token>" to {UPCTL_CONFIG_PATH}.')
    config = yaml_utils.read_yaml(config_path) or {}
    token = config.get('token') if isinstance(config, dict) else None
    if not token or not str(token).strip():
        raise UpCloudCredentialsNotFound(
            f'No token found in {UPCTL_CONFIG_PATH}. Expected '
            '"token: <api-token>".')
    return str(token).strip()


def credential_file_exists() -> bool:
    return os.path.exists(os.path.expanduser(UPCTL_CONFIG_PATH))


def upcloud_profile_in_aws_cred_and_config() -> bool:
    """Return whether the UpCloud Object Storage AWS profile is configured."""
    credentials_path = os.path.expanduser('~/.aws/credentials')
    profile_in_credentials = False
    if os.path.isfile(credentials_path):
        with open(credentials_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{UPCLOUD_PROFILE_NAME}]' in line:
                    profile_in_credentials = True
                    break

    config_path = os.path.expanduser('~/.aws/config')
    profile_in_config = False
    if os.path.isfile(config_path):
        with open(config_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {UPCLOUD_PROFILE_NAME}]' in line:
                    profile_in_config = True
                    break

    return profile_in_credentials and profile_in_config


def make_client(token: Optional[str] = None):
    """Return an authenticated requests session for the given token."""
    # Import locally to avoid a circular import while sky is initialized.
    # pylint: disable=import-outside-toplevel
    from sky import __version__
    api_token = token or get_api_token()
    client_session = requests.Session()
    client_session.headers.update({
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': f'skypilot/{__version__}',
    })
    return client_session


@annotations.lru_cache(scope='global', maxsize=1)
def client():
    """Return a cached authenticated UpCloud API client."""
    return make_client()


def get_account():
    """Returns the account details of the authenticated token.

    Raises:
        UpCloudError: if the token is invalid or the request fails.
    """
    client_session = client()
    try:
        response = client_session.get(f'{API_BASE}/account', timeout=120)
    except requests.exceptions.RequestException as e:
        raise UpCloudError(f'UpCloud account request failed: {e}') from e
    if response.status_code in (401, 403):
        raise UpCloudError(
            'UpCloud API token is invalid or missing account access. '
            'Create a token in the UpCloud Hub.')
    if not response.ok:
        raise UpCloudError(f'UpCloud account check failed: {response.text}')
    return response.json()


def check_compute_credentials() -> bool:
    """Validate UpCloud compute credentials with GET /account."""
    get_account()
    return True


def get_upcloud_credentials(boto3_session):
    """Return frozen credentials from an UpCloud Object Storage session."""
    credentials = boto3_session.get_credentials()
    if credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'UpCloud Object Storage credentials not found. Run '
                '`sky check` to verify credentials are correctly set up.')
    return credentials.get_frozen_credentials()


@common.load_lazy_modules(modules=_STORAGE_LAZY_MODULES)
@annotations.lru_cache(scope='global')
def session():
    """Create a boto3 session for UpCloud Managed Object Storage."""
    with _session_creation_lock:
        return boto3.session.Session(profile_name=UPCLOUD_PROFILE_NAME)


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a boto3 resource for UpCloud Object Storage."""
    return session().resource(resource_name, **kwargs)


@annotations.lru_cache(scope='global')
def s3_client(service_name: str = 's3'):
    """Create a boto3 client for UpCloud Object Storage."""
    return session().client(service_name)


@common.load_lazy_modules(modules=_STORAGE_LAZY_MODULES)
def botocore_exceptions():
    """botocore exceptions module."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions
    return exceptions
