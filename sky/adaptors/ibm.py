"""IBM cloud adaptors"""

# pylint: disable=import-outside-toplevel

import json
import multiprocessing
import os

from sky import sky_logging
from sky.adaptors import common

CREDENTIAL_FILE = '~/.ibm/credentials.yaml'
logger = sky_logging.init_logger(__name__)

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for IBM. '
                         'Try running: pip install "skypilot[ibm]".\n')
ibm_vpc = common.LazyImport('ibm_vpc',
                            import_error_message=_IMPORT_ERROR_MESSAGE)
ibm_cloud_sdk_core = common.LazyImport(
    'ibm_cloud_sdk_core', import_error_message=_IMPORT_ERROR_MESSAGE)
ibm_platform_services = common.LazyImport(
    'ibm_platform_services', import_error_message=_IMPORT_ERROR_MESSAGE)
ibm_boto3 = common.LazyImport('ibm_boto3',
                              import_error_message=_IMPORT_ERROR_MESSAGE)
ibm_botocore = common.LazyImport('ibm_botocore',
                                 import_error_message=_IMPORT_ERROR_MESSAGE)
requests = common.LazyImport('requests',
                             import_error_message=_IMPORT_ERROR_MESSAGE)
yaml = common.LazyImport('yaml', import_error_message=_IMPORT_ERROR_MESSAGE)

# Global process lock for thread-safe boto3 operations
global_process_lock = None


def read_credential_file():
    try:
        with open(os.path.expanduser(CREDENTIAL_FILE), 'r',
                  encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}


def get_api_key():
    return read_credential_file()['iam_api_key']


def get_hmac_keys():
    cred_file = read_credential_file()
    return cred_file['access_key_id'], cred_file['secret_access_key']


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


def search_client():
    return ibm_platform_services.GlobalSearchV2(
        authenticator=_get_authenticator())


def tagging_client():
    return ibm_platform_services.GlobalTaggingV1(
        authenticator=_get_authenticator())


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
    global global_process_lock

    if global_process_lock is None:
        global_process_lock = multiprocessing.Lock()

    return global_process_lock
