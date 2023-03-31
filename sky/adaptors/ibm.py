"""IBM cloud adaptors"""

# pylint: disable=import-outside-toplevel

from sky import sky_logging
import yaml
import os
import json
import requests
import functools

CREDENTIAL_FILE = '~/.ibm/credentials.yaml'
logger = sky_logging.init_logger(__name__)

ibm_vpc = None
ibm_cloud_sdk_core = None
ibm_platform_services = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global ibm_vpc, ibm_cloud_sdk_core, ibm_platform_services
        if None in [ibm_vpc, ibm_cloud_sdk_core, ibm_platform_services]:
            try:
                import ibm_vpc as _ibm_vpc
                import ibm_cloud_sdk_core as _ibm_cloud_sdk_core
                import ibm_platform_services as _ibm_platform_services
                ibm_vpc = _ibm_vpc
                ibm_cloud_sdk_core = _ibm_cloud_sdk_core
                ibm_platform_services = _ibm_platform_services
            except ImportError:
                raise ImportError(
                    'Failed to import dependencies for IBM. '
                    'Try running: pip install "skypilot[ibm]".\n') from None
        return func(*args, **kwargs)

    return wrapper


def read_credential_file():
    with open(os.path.expanduser(CREDENTIAL_FILE), encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_api_key():
    return read_credential_file()['iam_api_key']


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
