


from functools import wraps
from sky import sky_logging
import yaml
import ibm_cloud_sdk_core
import ibm_vpc
from ibm_platform_services import GlobalSearchV2, GlobalTaggingV1
import os

CREDENTIAL_FILE ='~/.ibm/credentials.yaml'
logger = sky_logging.init_logger(__name__)


def get_authenticator():
    with open(os.path.expanduser(CREDENTIAL_FILE)) as f:
        base_config = yaml.safe_load(f)
    return ibm_cloud_sdk_core.authenticators.IAMAuthenticator(base_config['iam_api_key'])

def client(**kwargs):
    """returns ibm vpc client"""

    try:
        # Authenticate user - occurs once throughout the program 
        vpc_client = ibm_vpc.VpcV1(version='2022-06-30',authenticator=get_authenticator())
        if kwargs.get('region'):
            vpc_client.set_service_url(f'https://{kwargs["region"]}.iaas.cloud.ibm.com' + '/v1')
    except Exception:
        logger.error("No registered API key found matching specified value")
        raise  

    return vpc_client # returns either formerly or newly created client

def search_client():
    return GlobalSearchV2(authenticator=get_authenticator())

def tagging_client():
    return GlobalTaggingV1(authenticator=get_authenticator())