"""OCI Object Storage S3-compatible API adaptor.

OCI Object Storage exposes an Amazon S3 Compatibility API, authenticated
with a Customer Secret Key (an access key / secret key pair) instead of
the native OCI API key:
https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm

This adaptor accesses OCI Object Storage through that API with boto3,
using AWS-style credential/config files:
    ~/.oci/s3.credentials
        [oci]
        aws_access_key_id = ...
        aws_secret_access_key = ...
    ~/.oci/s3.config
        [profile oci]
        endpoint_url = https://<namespace>.compat.objectstorage.\
            <region>.oci.customer-oci.com
        region = <region>

Requests use path-style addressing (the bucket in the URL path, not the
hostname): it is what the namespaced compat endpoint expects, and it works
for every legal OCI bucket name — names with uppercase or underscores are
not valid DNS labels, so virtual-hosted addressing cannot reach them.

The presence of both files opts the deployment into the S3-compatible API
for `oci://` storage (see use_s3_api()); without them, SkyPilot uses the
native OCI SDK/CLI.
"""

import configparser
import contextlib
import os
import threading
from typing import Dict, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

OCI_S3_PROFILE_NAME = 'oci'
OCI_S3_CREDENTIALS_PATH = '~/.oci/s3.credentials'
OCI_S3_CONFIG_PATH = '~/.oci/s3.config'
NAME = 'OCI'
_INDENT_PREFIX = '    '

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for OCI S3 '
                         'compatibility API. Try pip install "skypilot[aws]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


@contextlib.contextmanager
def _load_oci_s3_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    prev_config_path = os.environ.get('AWS_CONFIG_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = OCI_S3_CREDENTIALS_PATH
    os.environ['AWS_CONFIG_FILE'] = OCI_S3_CONFIG_PATH
    try:
        yield
    finally:
        if prev_credentials_path is None:
            del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        else:
            os.environ['AWS_SHARED_CREDENTIALS_FILE'] = prev_credentials_path
        if prev_config_path is None:
            del os.environ['AWS_CONFIG_FILE']
        else:
            os.environ['AWS_CONFIG_FILE'] = prev_config_path


def use_s3_api() -> bool:
    """Whether to access OCI Object Storage via the S3-compatible API.

    True iff the S3-compatible credential and config files both exist with
    the expected profile. Their presence opts the deployment into the
    S3-compatible API for `oci://` storage; otherwise SkyPilot uses the
    native OCI SDK/CLI.
    """
    return oci_s3_profile_in_cred() and oci_s3_profile_in_config()


def get_oci_s3_credentials(boto3_session):
    """Gets the OCI S3 credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the OCI
        Customer Secret Key credentials.
    """
    with _load_oci_s3_credentials_env():
        oci_s3_credentials = boto3_session.get_credentials()
        if oci_s3_credentials is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('OCI S3-compatible credentials not found. '
                                 'Run `sky check` to verify credentials are '
                                 'correctly set up.')
        return oci_s3_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session for OCI Object Storage."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        with _load_oci_s3_credentials_env():
            session_ = boto3.session.Session(profile_name=OCI_S3_PROFILE_NAME)
        return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create an OCI S3-compatible resource.

    Args:
        resource_name: resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    oci_s3_credentials = get_oci_s3_credentials(session_)
    endpoint = get_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint,
        aws_access_key_id=oci_s3_credentials.access_key,
        aws_secret_access_key=oci_s3_credentials.secret_key,
        region_name=get_region(),
        config=botocore.config.Config(
            s3={'addressing_style': 'path'},
            # OCI's S3-compatible API returns 501 for uploads that use
            # aws-chunked content encoding, which newer AWS SDKs enable by
            # default to carry a trailing integrity checksum. Restrict
            # checksum calculation to when_required so PutObject is sent as
            # a plain (non-chunked) request. Response validation is relaxed
            # to match, so downloads of checksummed objects aren't rejected.
            # https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi_topic-Amazon_S3_Compatibility_API_Support.htm
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required'),
        **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create an OCI S3-compatible client of a certain service.

    Args:
        service_name: service name (e.g., 's3').
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    oci_s3_credentials = get_oci_s3_credentials(session_)
    endpoint = get_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=oci_s3_credentials.access_key,
        aws_secret_access_key=oci_s3_credentials.secret_key,
        region_name=get_region(),
        config=botocore.config.Config(
            s3={'addressing_style': 'path'},
            # OCI's S3-compatible API returns 501 for uploads that use
            # aws-chunked content encoding, which newer AWS SDKs enable by
            # default to carry a trailing integrity checksum. Restrict
            # checksum calculation to when_required so PutObject is sent as
            # a plain (non-chunked) request. Response validation is relaxed
            # to match, so downloads of checksummed objects aren't rejected.
            # https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi_topic-Amazon_S3_Compatibility_API_Support.htm
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required'),
    )


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions as boto_exceptions
    return boto_exceptions


def _read_config_option(option: str) -> Optional[str]:
    """Reads an option from the [profile oci] section of OCI_S3_CONFIG_PATH."""
    config_path = os.path.expanduser(OCI_S3_CONFIG_PATH)
    if not os.path.isfile(config_path):
        return None
    try:
        config = configparser.ConfigParser()
        config.read(config_path)
        profile_section = f'profile {OCI_S3_PROFILE_NAME}'
        if config.has_section(profile_section):
            if config.has_option(profile_section, option):
                return config.get(profile_section, option).strip()
    except (configparser.Error, OSError) as e:
        logger.warning(f'Failed to parse OCI S3-compatible config file: {e}.')
    return None


def get_endpoint() -> str:
    """Parse the OCI_S3_CONFIG_PATH to get the endpoint_url.

    Unlike providers with a global endpoint, the OCI S3-compatible endpoint
    embeds the tenancy's Object Storage namespace and region, e.g.
    https://<namespace>.compat.objectstorage.<region>.oci.customer-oci.com,
    so there is no default to fall back to.

    Raises:
        ValueError: If the endpoint_url is not found in the config file.
    """
    endpoint = _read_config_option('endpoint_url')
    if endpoint is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'OCI S3-compatible endpoint not found. Set endpoint_url '
                f'under [profile {OCI_S3_PROFILE_NAME}] in '
                f'{OCI_S3_CONFIG_PATH}, e.g. https://<namespace>.compat.'
                'objectstorage.<region>.oci.customer-oci.com')
    return endpoint


def get_region() -> Optional[str]:
    """Parse the OCI_S3_CONFIG_PATH to get the region, if set.

    The region is used for SigV4 signing and must be the OCI region
    identifier embedded in the endpoint (e.g. us-sanjose-1).
    """
    return _read_config_option('region')


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    else:
        raise exceptions.NotSupportedError(
            f'{NAME} S3-compatible API does not support {cloud_capability}.')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks credentials for OCI Object Storage via the S3-compatible API.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when both credentials needed for OCI S3-compatible storage
        are set. It is False when either of those are not set, which would
        hint with a string on the unset credential.
    """
    hints = None
    profile_in_cred = oci_s3_profile_in_cred()
    profile_in_config = oci_s3_profile_in_config()

    if not profile_in_cred:
        hints = (f'[{OCI_S3_PROFILE_NAME}] profile is not set in '
                 f'{OCI_S3_CREDENTIALS_PATH}.')
    if not profile_in_config:
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += (f'[profile {OCI_S3_PROFILE_NAME}] is not set in '
                  f'{OCI_S3_CONFIG_PATH}.')

    if hints:
        hints += (' Generate a Customer Secret Key in the OCI console, then'
                  ' run the following commands:')
        if not profile_in_cred:
            hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_SHARED_CREDENTIALS_FILE='
                      f'{OCI_S3_CREDENTIALS_PATH} aws configure --profile '
                      f'{OCI_S3_PROFILE_NAME}')
        if not profile_in_config:
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_CONFIG_FILE='
                      f'{OCI_S3_CONFIG_PATH} aws configure set endpoint_url'
                      ' https://<namespace>.compat.objectstorage.<region>'
                      '.oci.customer-oci.com --profile '
                      f'{OCI_S3_PROFILE_NAME}')
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_CONFIG_FILE='
                      f'{OCI_S3_CONFIG_PATH} aws configure set region '
                      f'<region> --profile {OCI_S3_PROFILE_NAME}')
        hints += f'\n{_INDENT_PREFIX}For more info: '
        hints += 'https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm'  # pylint: disable=line-too-long

    return (False, hints) if hints else (True, hints)


def oci_s3_profile_in_config() -> bool:
    """Checks if the OCI S3 profile is set in the config file."""
    conf_path = os.path.expanduser(OCI_S3_CONFIG_PATH)
    profile_exists = False
    if os.path.isfile(conf_path):
        with open(conf_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {OCI_S3_PROFILE_NAME}]' in line:
                    profile_exists = True
                    break
    return profile_exists


def oci_s3_profile_in_cred() -> bool:
    """Checks if the OCI S3 profile is set in the credentials file."""
    cred_path = os.path.expanduser(OCI_S3_CREDENTIALS_PATH)
    profile_exists = False
    if os.path.isfile(cred_path):
        with open(cred_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{OCI_S3_PROFILE_NAME}]' in line:
                    profile_exists = True
                    break
    return profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for OCI S3-compatible storage.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    return {
        OCI_S3_CREDENTIALS_PATH: OCI_S3_CREDENTIALS_PATH,
        OCI_S3_CONFIG_PATH: OCI_S3_CONFIG_PATH,
    }
