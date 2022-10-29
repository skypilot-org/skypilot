


from functools import wraps
from sky.clouds import ibm
import os
import yaml


ibm_cloud_sdk_core=None
ibm_vpc=None
client = None

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

    if not client:
        if not os.path.isfile(os.path.expanduser("~/.ibm/credentials")):
            with open("~/.ibm/credentials") as f:
                    base_config = yaml.safe_load(f)
        return ibm_vpc.VpcV1(authenticator=ibm_cloud_sdk_core.authenticators.IAMAuthenticator(base_config['iam_api_key']))
    else:
        return client