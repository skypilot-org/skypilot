""" Seeweb Adaptor """
import configparser
import os
import pathlib
import threading
from typing import Optional

import pydantic
import requests  # type: ignore

from sky import sky_logging
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils


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

SEEWEB_PROFILE_NAME = 'seeweb'
SEEWEB_CREDENTIALS_PATH = '~/.aws/credentials'
SEEWEB_CONFIG_PATH = '~/.aws/config'
_ENDPOINT_ENV_VAR = 'SEEWEB_S3_ENDPOINT'
_INDENT_PREFIX = '    '

_session_creation_lock = threading.RLock()
logger = sky_logging.init_logger(__name__)


def _get_credentials_path() -> str:
    return os.path.expanduser(
        os.environ.get('AWS_SHARED_CREDENTIALS_FILE', SEEWEB_CREDENTIALS_PATH))


def _get_config_path() -> str:
    return os.path.expanduser(
        os.environ.get('AWS_CONFIG_FILE', SEEWEB_CONFIG_PATH))


def _profile_exists(path: str, header: str) -> bool:
    if not os.path.isfile(path):
        return False
    section = f'[{header}]'
    with open(path, 'r', encoding='utf-8') as file:
        for line in file:
            if line.strip() == section:
                return True
    return False


def _extract_endpoint_from_block(block: str) -> Optional[str]:
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if 'endpoint_url' in line:
            _, _, value = line.partition('=')
            value = value.strip()
            if value:
                return value
    return None


def _profile_in_credentials() -> bool:
    return _profile_exists(_get_credentials_path(), SEEWEB_PROFILE_NAME)


def _profile_in_config() -> bool:
    return _profile_exists(_get_config_path(), f'profile {SEEWEB_PROFILE_NAME}')


def get_endpoint() -> str:
    """Return the Seeweb Object Storage endpoint URL."""
    env_override = os.environ.get(_ENDPOINT_ENV_VAR)
    if env_override:
        endpoint = env_override.strip()
        return endpoint

    config_path = _get_config_path()
    if os.path.isfile(config_path):
        parser = configparser.ConfigParser()
        try:
            parser.read(config_path)
            section = f'profile {SEEWEB_PROFILE_NAME}'
            if parser.has_section(section):
                for key in ('endpoint_url', 'seeweb_endpoint_url'):
                    if parser.has_option(section, key):
                        value = parser.get(section, key).strip()
                        if value:
                            return value
                if parser.has_option(section, 's3'):
                    block = parser.get(section, 's3')
                    endpoint_value = _extract_endpoint_from_block(block)
                    if endpoint_value:
                        return endpoint_value
        except configparser.Error:
            pass

    env_fallback = os.environ.get('SEEWEB_ENDPOINT_URL')
    if env_fallback:
        endpoint = env_fallback.strip()
        return endpoint

    # No endpoint configured - raise an error
    with ux_utils.print_exception_no_traceback():
        raise SeewebCredentialsFileNotFound(
            'Seeweb S3 endpoint not configured. Set the endpoint via:\n'
            f'{_INDENT_PREFIX}aws configure set endpoint_url '
            f'<SEEWEB_ENDPOINT> --profile {SEEWEB_PROFILE_NAME}\n'
            f'{_INDENT_PREFIX}OR set environment variable '
            f'{_ENDPOINT_ENV_VAR}=<SEEWEB_ENDPOINT>')


def get_seeweb_credentials(boto3_session):
    """Gets Seeweb Object Storage credentials from boto3 session."""
    seeweb_credentials = boto3_session.get_credentials()
    if seeweb_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise SeewebCredentialsFileNotFound(
                'Seeweb S3 credentials not found. Configure the '
                f'[{SEEWEB_PROFILE_NAME}] profile via '
                f'`aws configure --profile {SEEWEB_PROFILE_NAME}`.')
    return seeweb_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create a boto3 session for Seeweb Object Storage."""
    with _session_creation_lock:
        sess = boto3.session.Session(profile_name=SEEWEB_PROFILE_NAME)
        return sess


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a Seeweb S3 resource (e.g., 's3')."""
    session_ = session()
    creds = get_seeweb_credentials(session_)
    endpoint = get_endpoint()
    config = botocore.config.Config(s3={'addressing_style': 'path'})
    return session_.resource(resource_name,
                             endpoint_url=endpoint,
                             aws_access_key_id=creds.access_key,
                             aws_secret_access_key=creds.secret_key,
                             config=config,
                             **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create a Seeweb S3-compatible client (e.g., 's3')."""
    session_ = session()
    creds = get_seeweb_credentials(session_)
    endpoint = get_endpoint()
    config = botocore.config.Config(s3={'addressing_style': 'path'})
    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        config=config,
    )


@common.load_lazy_modules(_LAZY_MODULES)
def check_compute_credentials() -> bool:
    """Checks if the user has access credentials to Seeweb's compute service.

    Returns True if credentials are valid; otherwise raises a SeewebError.
    """
    # Read API key from standard Seeweb configuration file
    key_path = pathlib.Path('~/.seeweb_cloud/seeweb_keys').expanduser()
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
        try:
            seeweb_client.fetch_servers()
        except pydantic.ValidationError:
            # Fallback: fetch raw JSON to validate authentication
            # pylint: disable=protected-access
            base_url = seeweb_client._Api__generate_base_url()  # type: ignore
            headers = seeweb_client._Api__generate_authentication_headers(
            )  # type: ignore
            url = f'{base_url}/servers'
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            # If we get here, authentication worked even if schema mismatches
    except Exception as e:  # pylint: disable=broad-except
        raise SeewebAuthenticationError(
            f'Unable to authenticate with Seeweb API: {e}') from e

    return True


@common.load_lazy_modules(_LAZY_MODULES)
def check_storage_credentials() -> bool:
    """Checks if the user has access credentials to Seeweb's storage service.
    """
    hints = []
    if not _profile_in_credentials():
        hints.append(
            f'[{SEEWEB_PROFILE_NAME}] profile missing in '
            f'{_get_credentials_path()}. Run:\n'
            f'{_INDENT_PREFIX}aws configure --profile {SEEWEB_PROFILE_NAME}')
    if not _profile_in_config():
        hints.append(f'[{SEEWEB_PROFILE_NAME}] profile missing in '
                     f'{_get_config_path()}. Set endpoint via:\n'
                     f'{_INDENT_PREFIX}aws configure set endpoint_url '
                     f'<SEEWEB_ENDPOINT> --profile {SEEWEB_PROFILE_NAME}')

    if hints:
        raise SeewebCredentialsFileNotFound('\n'.join(hints))

    try:
        s3_client = client('s3')
        s3_client.list_buckets()
    except Exception as e:  # pylint: disable=broad-except
        raise SeewebAuthenticationError(
            'Unable to authenticate with Seeweb Object Storage. '
            'Verify credentials and endpoint.') from e
    return True


@common.load_lazy_modules(_LAZY_MODULES)
@annotations.lru_cache(scope='global', maxsize=1)
def ecs_client():
    """Returns an authenticated ecsapi.Api object for compute APIs."""
    # Create authenticated client using the same credential pattern
    key_path = pathlib.Path('~/.seeweb_cloud/seeweb_keys').expanduser()
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

    api = ecsapi.Api(token=api_key)

    # Monkey-patch fetch_servers to be tolerant to API schema mismatches.
    orig_fetch_servers = api.fetch_servers
    orig_delete_server = api.delete_server

    def _tolerant_fetch_servers(
            timeout: Optional[int] = None):  # type: ignore[override]
        try:
            return orig_fetch_servers(timeout=timeout)
        except pydantic.ValidationError:
            # Fallback path: fetch raw JSON, drop snapshot fields, then validate
            # pylint: disable=protected-access
            base_url = api._Api__generate_base_url()  # type: ignore
            headers = api._Api__generate_authentication_headers(
            )  # type: ignore
            url = f'{base_url}/servers'
            resp = requests.get(url, headers=headers, timeout=timeout or 15)
            resp.raise_for_status()
            data = resp.json()
            try:
                servers = data.get('server', [])
                for s in servers:
                    s.pop('last_restored_snapshot', None)
                    group = s.get('group')
                    if isinstance(group, dict):
                        group_name = group.get('name')
                        s['group'] = group_name if isinstance(
                            group_name, str) else str(group_name)
            except (KeyError, TypeError, ValueError):
                pass
            server_list_response_cls = ecsapi._server._ServerListResponse
            servers_response = server_list_response_cls.model_validate(data)
            return servers_response.server

    api.fetch_servers = _tolerant_fetch_servers  # type: ignore[assignment]

    def _tolerant_delete_server(server_name: str,
                                timeout: Optional[int] = None):
        try:
            return orig_delete_server(server_name, timeout=timeout)
        except pydantic.ValidationError:
            # Fallback: perform raw DELETE and interpret not_found as success
            # pylint: disable=protected-access
            base_url = api._Api__generate_base_url()  # type: ignore
            headers = api._Api__generate_authentication_headers(
            )  # type: ignore
            url = f'{base_url}/servers/{server_name}'
            resp = requests.delete(url, headers=headers, timeout=timeout or 15)
            # Treat 404 as idempotent success
            if resp.status_code == 404:
                return None
            # Some APIs return {status: 'not_found', message: ...}
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get('status') == 'not_found':
                    return None
            except (ValueError, TypeError):
                pass
            # If not clearly not_found, re-raise original behavior
            resp.raise_for_status()
            # Best-effort: return None to indicate deletion requested
            return None

    api.delete_server = _tolerant_delete_server  # type: ignore[assignment]
    return api
