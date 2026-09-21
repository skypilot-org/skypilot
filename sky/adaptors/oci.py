"""Oracle OCI cloud adaptor"""

import functools
import logging
import os
from typing import Any, Dict, Optional, Tuple

from sky.adaptors import common
from sky.clouds.utils import oci_utils

# Suppress OCI circuit breaker logging before lazy import, because
# oci modules prints additional message during imports, i.e., the
# set_logger in the LazyImport called after imports will not take
# effect.
logging.getLogger('oci.circuit_breaker').setLevel(logging.WARNING)

OCI_CONFIG_PATH = '~/.oci/config'
ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'
# Written by `oci session authenticate`; API-key profiles never carry it.
SECURITY_TOKEN_FILE_KEY = 'security_token_file'

oci = common.LazyImport(
    'oci',
    import_error_message='Failed to import dependencies for OCI. '
    'Try running: pip install "skypilot[oci]"')


class OCISessionTokenError(Exception):
    """A session-token profile (`oci session authenticate`) is unusable.

    Raised when the profile's `security_token_file` is missing or the token
    in it has already expired; the message tells the user how to start a
    new session. Independent of the `oci` SDK so callers can catch it
    without importing the SDK.
    """


def get_config_file() -> str:
    conf_file_path = OCI_CONFIG_PATH
    config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
    if config_path_via_env_var is not None:
        conf_file_path = config_path_via_env_var
    return conf_file_path


def _resolve_profile(profile: Optional[str],
                     region: Optional[str] = None) -> str:
    """The `~/.oci/config` profile to use.

    `DEFAULT` defers to `oci_config_profile` in `~/.sky/config.yaml`, where a
    region can name its own profile, so the region is passed along whenever
    the caller knows it.
    """
    if not profile or profile == 'DEFAULT':
        return oci_utils.oci_config.get_profile(region)
    return profile


def get_oci_config(region=None, profile='DEFAULT'):
    conf_file_path = get_config_file()
    config_profile = _resolve_profile(profile, region)

    oci_config = oci.config.from_file(file_location=conf_file_path,
                                      profile_name=config_profile)
    if region is not None:
        oci_config['region'] = region

    return oci_config


def is_session_token_config(oci_config: Dict[str, Any]) -> bool:
    """Whether the loaded profile authenticates with a session token."""
    return bool(oci_config.get(SECURITY_TOKEN_FILE_KEY))


def session_authenticate_hint(profile: str) -> str:
    """How to get a fresh token for a session-token profile."""
    return (f'Run `oci session authenticate --profile-name {profile}` (or '
            f'`oci session refresh --profile {profile}` while the session '
            'is still refreshable) to get a new token.')


def _session_token_expired(token: str) -> bool:
    try:
        container = oci.auth.security_token_container.SecurityTokenContainer(
            None, token)
    except Exception:  # pylint: disable=broad-except
        # Not a JWT we can inspect locally; let the service judge it.
        return False
    return not container.valid()


def get_oci_signer(oci_config: Dict[str, Any],
                   profile: str = 'DEFAULT',
                   region: Optional[str] = None):
    """Returns the request signer for `oci_config`, or None for API keys.

    `oci session authenticate` writes profiles that carry a
    `security_token_file` and a session `key_file` but no `user` or
    `fingerprint`. The SDK rejects such a config unless the signer is
    supplied explicitly, so build the `SecurityTokenSigner` here, mirroring
    what the OCI CLI does for `--auth security_token`. API-key profiles
    return None and keep passing the plain config dict to the clients.

    Raises:
        OCISessionTokenError: the token file is missing or the token has
            expired.
    """
    token_file = oci_config.get(SECURITY_TOKEN_FILE_KEY)
    if not token_file:
        return None
    profile = _resolve_profile(profile, region)
    token_path = os.path.expanduser(token_file)
    if not os.path.isfile(token_path):
        raise OCISessionTokenError(
            f'OCI profile {profile!r} uses a session token, but the token '
            f'file {token_file} does not exist. '
            f'{session_authenticate_hint(profile)}')
    with open(token_path, 'r', encoding='utf-8') as f:
        token = f.read().strip()
    if _session_token_expired(token):
        raise OCISessionTokenError(
            f'The OCI session token for profile {profile!r} ({token_file}) '
            f'has expired. {session_authenticate_hint(profile)}')
    private_key = oci.signer.load_private_key_from_file(
        oci_config['key_file'], oci_config.get('pass_phrase'))
    return oci.auth.signers.SecurityTokenSigner(token, private_key)


def _get_client_args(region, profile) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Config and keyword arguments to construct an OCI SDK client."""
    oci_config = get_oci_config(region, profile)
    kwargs: Dict[str, Any] = {}
    signer = get_oci_signer(oci_config, profile, region)
    if signer is not None:
        kwargs['signer'] = signer
    return oci_config, kwargs


def get_core_client(region=None, profile='DEFAULT'):
    oci_config, kwargs = _get_client_args(region, profile)
    return oci.core.ComputeClient(oci_config, **kwargs)


def get_net_client(region=None, profile='DEFAULT'):
    oci_config, kwargs = _get_client_args(region, profile)
    return oci.core.VirtualNetworkClient(oci_config, **kwargs)


def get_search_client(region=None, profile='DEFAULT'):
    oci_config, kwargs = _get_client_args(region, profile)
    return oci.resource_search.ResourceSearchClient(oci_config, **kwargs)


def get_identity_client(region=None, profile='DEFAULT'):
    oci_config, kwargs = _get_client_args(region, profile)
    return oci.identity.IdentityClient(oci_config, **kwargs)


def get_object_storage_client(region=None, profile='DEFAULT'):
    oci_config, kwargs = _get_client_args(region, profile)
    return oci.object_storage.ObjectStorageClient(oci_config, **kwargs)


def service_exception():
    """OCI service exception."""
    return oci.exceptions.ServiceError


def with_oci_env(f):
    """Wraps a function to return a single shell command string (joined by '&&')
    that ensures OCI CLI is available before running the actual OCI
    command returned by `f`.
    """

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        oci_venv_dir = '"$HOME/sky-oci-cli-env"'
        enter_env_cmds = [
            # Create the venv if missing
            (f'[ -d {oci_venv_dir} ] || '
             f'uv venv --seed {oci_venv_dir} --python 3.10'),
            f'source {oci_venv_dir}/bin/activate',
            'uv pip install oci-cli',
            'export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True',
        ]
        operation_cmd = [f(*args, **kwargs)]
        leave_env_cmds = ['deactivate']
        return ' && '.join(enter_env_cmds + operation_cmd + leave_env_cmds)

    return wrapper
