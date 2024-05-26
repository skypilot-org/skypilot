"""Cloudflare cloud adaptors"""
# pylint: disable=import-outside-toplevel

import contextlib
import functools
import os
import threading
from typing import Dict, Optional, Tuple

from sky.adaptors import common
from sky.utils import ux_utils

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Cloudflare.'
                         'Try pip install "skypilot[cloudflare]"')
boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (boto3, botocore)

_session_creation_lock = threading.RLock()
ACCOUNT_ID_PATH = '~/.cloudflare/accountid'
R2_CREDENTIALS_PATH = '~/.cloudflare/r2.credentials'
R2_PROFILE_NAME = 'r2'
_INDENT_PREFIX = '    '
NAME = 'Cloudflare'
SKY_CHECK_NAME = 'Cloudflare (for R2 object store)'


@contextlib.contextmanager
def _load_r2_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = R2_CREDENTIALS_PATH
    try:
        yield
    finally:
        if prev_credentials_path is None:
            del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        else:
            os.environ['AWS_SHARED_CREDENTIALS_FILE'] = prev_credentials_path


def get_r2_credentials(boto3_session):
    """Gets the R2 credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the R2 credentials.
    """
    with _load_r2_credentials_env():
        cloudflare_credentials = boto3_session.get_credentials()
        if cloudflare_credentials is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Cloudflare credentials not found. Run '
                                 '`sky check` to verify credentials are '
                                 'correctly set up.')
        else:
            return cloudflare_credentials.get_frozen_credentials()


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache # pylint: disable=line-too-long
@functools.lru_cache()
def session():
    """Create an AWS session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        with _load_r2_credentials_env():
            session_ = boto3.session.Session(profile_name=R2_PROFILE_NAME)
        return session_


@functools.lru_cache()
def resource(resource_name: str, **kwargs):
    """Create a Cloudflare resource.

    Args:
        resource_name: Cloudflare resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    cloudflare_credentials = get_r2_credentials(session_)
    endpoint = create_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint,
        aws_access_key_id=cloudflare_credentials.access_key,
        aws_secret_access_key=cloudflare_credentials.secret_key,
        region_name='auto',
        **kwargs)


@functools.lru_cache()
def client(service_name: str, region):
    """Create an CLOUDFLARE client of a certain service.

    Args:
        service_name: CLOUDFLARE service name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    cloudflare_credentials = get_r2_credentials(session_)
    endpoint = create_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=cloudflare_credentials.access_key,
        aws_secret_access_key=cloudflare_credentials.secret_key,
        region_name=region)


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions


def create_endpoint():
    """Reads accountid necessary to interact with R2"""

    accountid_path = os.path.expanduser(ACCOUNT_ID_PATH)
    with open(accountid_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        accountid = lines[0]

    accountid = accountid.strip()
    endpoint = 'https://' + accountid + '.r2.cloudflarestorage.com'

    return endpoint


def check_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Cloudflare R2.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when both credentials needed for R2 is set. It is False
        when either of those are not set, which would hint with a
        string on unset credential.
    """

    hints = None
    accountid_path = os.path.expanduser(ACCOUNT_ID_PATH)
    if not r2_profile_in_aws_cred():
        hints = f'[{R2_PROFILE_NAME}] profile is not set in {R2_CREDENTIALS_PATH}.'
    if not os.path.exists(accountid_path):
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += 'Account ID from R2 dashboard is not set.'
    if hints:
        hints += ' Run the following commands:'
        if not r2_profile_in_aws_cred():
            hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
            hints += f'\n{_INDENT_PREFIX}  $ AWS_SHARED_CREDENTIALS_FILE={R2_CREDENTIALS_PATH} aws configure --profile r2'  # pylint: disable=line-too-long
        if not os.path.exists(accountid_path):
            hints += f'\n{_INDENT_PREFIX}  $ mkdir -p ~/.cloudflare'
            hints += f'\n{_INDENT_PREFIX}  $ echo <YOUR_ACCOUNT_ID_HERE> > ~/.cloudflare/accountid'  # pylint: disable=line-too-long
        hints += f'\n{_INDENT_PREFIX}For more info: '
        hints += 'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#cloudflare-r2'  # pylint: disable=line-too-long

    return (False, hints) if hints else (True, hints)


def r2_profile_in_aws_cred() -> bool:
    """Checks if Cloudflare R2 profile is set in aws credentials"""

    profile_path = os.path.expanduser(R2_CREDENTIALS_PATH)
    r2_profile_exists = False
    if os.path.isfile(profile_path):
        with open(profile_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{R2_PROFILE_NAME}]' in line:
                    r2_profile_exists = True
                    break
    return r2_profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Checks if aws credential file is set and update if not
       Updates file containing account ID information

    Args:
        file_mounts: stores path to credential files of clouds
    """

    r2_credential_mounts = {
        R2_CREDENTIALS_PATH: R2_CREDENTIALS_PATH,
        ACCOUNT_ID_PATH: ACCOUNT_ID_PATH
    }
    return r2_credential_mounts
