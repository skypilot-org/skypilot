""" Seeweb Adaptor """
import configparser
import pathlib
from typing import Optional

import pydantic
import requests  # type: ignore

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

    Mirrors compute credentials validation.
    """
    return check_compute_credentials()


@common.load_lazy_modules(_LAZY_MODULES)
@annotations.lru_cache(scope='global', maxsize=1)
def client():
    """Returns an authenticated ecsapi.Api object."""
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
