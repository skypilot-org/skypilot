"""IBM cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import json
import multiprocessing
import os

import requests
import yaml

from sky import sky_logging

CREDENTIAL_FILE = '~/.ibm/credentials.yaml'
logger = sky_logging.init_logger(__name__)

ibm_vpc = None
ibm_cloud_sdk_core = None
ibm_platform_services = None
ibm_boto3 = None
ibm_botocore = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global ibm_vpc, ibm_cloud_sdk_core, ibm_platform_services
        global ibm_boto3, ibm_botocore
        if None in [ibm_vpc, ibm_cloud_sdk_core, ibm_platform_services]:
            try:
                import ibm_boto3 as _ibm_boto3
                import ibm_botocore as _ibm_botocore
                import ibm_cloud_sdk_core as _ibm_cloud_sdk_core
                import ibm_platform_services as _ibm_platform_services
                import ibm_vpc as _ibm_vpc
                ibm_vpc = _ibm_vpc
                ibm_cloud_sdk_core = _ibm_cloud_sdk_core
                ibm_platform_services = _ibm_platform_services
                ibm_boto3 = _ibm_boto3
                ibm_botocore = _ibm_botocore
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for IBM. '
                    'Try running: pip install "skypilot[ibm]".\n') from None
        return func(*args, **kwargs)

    return wrapper


def read_credential_file():
    try:
        with open(os.path.expanduser(CREDENTIAL_FILE), 'r',
                  encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return False


def get_api_key():
    return read_credential_file()['iam_api_key']


def get_hmac_keys():
    cred_file = read_credential_file()
    return cred_file['access_key_id'], cred_file['secret_access_key']


@import_package
def _get_authenticator():
    return ibm_cloud_sdk_core.authenticators.IAMAuthenticator(get_api_key())


def get_oauth_token():
    """returns a temporary authentication token required by
        various IBM cloud APIs
    using an http request to avoid having to install module
        ibm_watson to get IAMTokenManager"""
    # pylint: disable=line-too-long
    res = requests.post(
        'https://iam.cloud.ibm.com/identity/token',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data=
        f'grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={get_api_key()}'
    )
    return json.loads(res.text)['access_token']


@import_package
def client(**kwargs):
    """Create an ibm vpc client.

    Sets the vpc client to a specific region.
    If none was specified 'us-south' is set internally.

    Args:
        kwargs: Keyword arguments.

    Returns:
        ibm vpc client
    """

    try:
        vpc_client = ibm_vpc.VpcV1(version='2022-06-30',
                                   authenticator=_get_authenticator())
        if kwargs.get('region'):
            vpc_client.set_service_url(
                f'https://{kwargs["region"]}.iaas.cloud.ibm.com/v1')
    except Exception:
        logger.error('No registered API key found matching specified value')
        raise

    return vpc_client  # returns either formerly or newly created client


@import_package
def search_client():
    return ibm_platform_services.GlobalSearchV2(
        authenticator=_get_authenticator())


@import_package
def tagging_client():
    return ibm_platform_services.GlobalTaggingV1(
        authenticator=_get_authenticator())


@import_package
def get_cos_client(region: str = 'us-east'):
    """Returns an IBM COS client object.
      Using process lock to protect not multi process
      safe Boto3.session, which is invoked (default session) by
      boto3.client.

    Args:
        region (str, optional): Client endpoint. Defaults to 'us-east'.

    Returns:
        IBM COS client
    """
    access_key_id, secret_access_key = get_hmac_keys()
    with _get_global_process_lock():
        return ibm_boto3.client(  # type: ignore[union-attr]
            service_name='s3',
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=
            f'https://s3.{region}.cloud-object-storage.appdomain.cloud')


@import_package
def get_cos_resource(region: str = 'us-east'):
    """Returns an IBM COS Resource object.
      Using process lock to protect not multi process safe
      boto3.Resource.

    Args:
        region (str, optional): Resource Endpoint. Defaults to 'us-east'.

    Returns:
        IBM COS Resource
    """
    access_key_id, secret_access_key = get_hmac_keys()
    with _get_global_process_lock():
        return ibm_boto3.resource(  # type: ignore[union-attr]
            's3',
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=
            f'https://s3.{region}.cloud-object-storage.appdomain.cloud')


def _get_global_process_lock():
    """returns a multi-process lock.

    lazily initializes a global multi-process lock if it wasn't
      already initialized.
    Necessary when process are spawned without a shared lock.
    """
    global global_process_lock  # pylint: disable=global-variable-undefined

    if 'global_process_lock' not in globals():
        global_process_lock = multiprocessing.Lock()

    return global_process_lock
