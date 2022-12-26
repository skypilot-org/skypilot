


from functools import wraps
from sky import sky_logging
import yaml
import os

CREDENTIAL_FILE = os.path.join(os.path.expanduser('~'),'.ibm','credentials.yaml')
logger = sky_logging.init_logger(__name__)
vpc_client = None
ibm_vpc = None
ibm_cloud_sdk_core = None

def import_package(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global ibm_cloud_sdk_core, ibm_vpc
        if ibm_cloud_sdk_core is None or ibm_vpc is None:
            try:
                import ibm_cloud_sdk_core as _ibm_cloud_sdk_core
                import ibm_vpc as _ibm_vpc
                ibm_cloud_sdk_core = _ibm_cloud_sdk_core
                ibm_vpc = _ibm_vpc
            except ImportError:
                raise ImportError('Fail to import dependencies for IBM.'
                                  'Try pip install "skypilot[ibm]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def client(**kwargs):
    """returns ibm vpc client"""
    global vpc_client

    if not vpc_client:
        with open(CREDENTIAL_FILE) as f:
                base_config = yaml.safe_load(f)
        try:
            # Authenticate user - occurs once throughout the program 
            vpc_client = ibm_vpc.VpcV1(version='2022-06-30',authenticator=ibm_cloud_sdk_core.authenticators.IAMAuthenticator(base_config['iam_api_key']))
            if kwargs.get('region'):
                vpc_client.set_service_url(f'https://{kwargs["region"]}.iaas.cloud.ibm.com' + '/v1')
        except Exception:
            logger.error("No registered API key found matching specified value")
            raise  

    elif kwargs.get('region'):
        vpc_client.set_service_url(f'https://{kwargs["region"]}.iaas.cloud.ibm.com' + '/v1')

    return vpc_client # returns either formerly or newly created client