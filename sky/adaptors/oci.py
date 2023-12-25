"""Oracle OCI cloud adaptor"""

import functools
import os

CONFIG_PATH = '~/.oci/config'
ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'

oci = None


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


def get_config_file() -> str:
    conf_file_path = CONFIG_PATH
    config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
    if config_path_via_env_var is not None:
        conf_file_path = config_path_via_env_var
    return conf_file_path


@import_package
def get_oci():
    return oci


@import_package
def get_oci_config(region=None, profile='DEFAULT'):
    conf_file_path = get_config_file()
    oci_config = oci.config.from_file(file_location=conf_file_path,
                                      profile_name=profile)
    if region is not None:
        oci_config['region'] = region
    return oci_config


@import_package
def get_core_client(region=None, profile='DEFAULT'):
    return oci.core.ComputeClient(get_oci_config(region, profile))


@import_package
def get_net_client(region=None, profile='DEFAULT'):
    return oci.core.VirtualNetworkClient(get_oci_config(region, profile))


@import_package
def get_search_client(region=None, profile='DEFAULT'):
    return oci.resource_search.ResourceSearchClient(
        get_oci_config(region, profile))


@import_package
def get_identity_client(region=None, profile='DEFAULT'):
    return oci.identity.IdentityClient(get_oci_config(region, profile))


@import_package
def service_exception():
    """OCI service exception."""
    # pylint: disable=import-outside-toplevel
    from oci.exceptions import ServiceError
    return ServiceError
