"""IBM cloud adaptors"""

from sky import sky_logging
import yaml
import ibm_cloud_sdk_core
import ibm_vpc
from ibm_platform_services import GlobalSearchV2, GlobalTaggingV1
import os
import json
import requests

CREDENTIAL_FILE = '~/.ibm/credentials.yaml'
logger = sky_logging.init_logger(__name__)


def read_credential_file():
    with open(os.path.expanduser(CREDENTIAL_FILE), encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_api_key():
    return read_credential_file()['iam_api_key']


def get_authenticator():
    return ibm_cloud_sdk_core.authenticators.IAMAuthenticator(get_api_key())


def get_oauth_token():
    """:returns a temporary authentication token required by
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
    """returns ibm vpc client"""

    try:
        vpc_client = ibm_vpc.VpcV1(version='2022-06-30',
                                   authenticator=get_authenticator())
        if kwargs.get('region'):
            vpc_client.set_service_url(
                f'https://{kwargs["region"]}.iaas.cloud.ibm.com/v1')
    except Exception:
        logger.error('No registered API key found matching specified value')
        raise

    return vpc_client  # returns either formerly or newly created client


def search_client():
    return GlobalSearchV2(authenticator=get_authenticator())


def tagging_client():
    return GlobalTaggingV1(authenticator=get_authenticator())
