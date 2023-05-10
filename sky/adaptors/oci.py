"""Oracle OCI cloud adaptor"""

import functools
import os

CONFIG_PATH = '~/.oci/config'
ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'

oci = None
oci_config = None
core_client = None
net_client = None
search_client = None
identity_client = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global oci
        if oci is None:
            try:
                # pylint: disable=import-outside-toplevel
                import oci as _oci
                oci = _oci
            except ImportError:
                raise ImportError('Fail to import dependencies for OCI.'
                                  'Try pip install "skypilot[oci]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_oci_config():
    global oci_config
    if oci_config is None:
        conf_file_path = CONFIG_PATH
        config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
        if config_path_via_env_var is not None:
            conf_file_path = config_path_via_env_var
        oci_config = oci.config.from_file(file_location=conf_file_path)
    return oci_config


@import_package
def get_core_client():
    global core_client
    if core_client is None:
        core_client = oci.core.ComputeClient(get_oci_config())
    return core_client


@import_package
def get_net_client():
    global net_client
    if net_client is None:
        net_client = oci.core.VirtualNetworkClient(get_oci_config())
    return net_client


@import_package
def get_search_client():
    global search_client
    if search_client is None:
        search_client = oci.resource_search.ResourceSearchClient(
            get_oci_config())
    return search_client


@import_package
def get_identity_client():
    global identity_client
    if identity_client is None:
        identity_client = oci.identity.IdentityClient(get_oci_config())
    return identity_client


@import_package
def service_exception():
    """OCI service exception."""
    # pylint: disable=import-outside-toplevel
    from oci.exceptions import ServiceError
    return ServiceError
