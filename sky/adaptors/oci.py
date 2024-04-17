"""Oracle OCI cloud adaptor"""

import os

from sky.adaptors import common

CONFIG_PATH = '~/.oci/config'
ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'

oci = common.LazyImport(
    'oci',
    import_error_message='Failed to import dependencies for OCI. '
    'Try running: pip install "skypilot[oci]"')


def get_config_file() -> str:
    conf_file_path = CONFIG_PATH
    config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
    if config_path_via_env_var is not None:
        conf_file_path = config_path_via_env_var
    return conf_file_path


def get_oci_config(region=None, profile='DEFAULT'):
    conf_file_path = get_config_file()
    oci_config = oci.config.from_file(file_location=conf_file_path,
                                      profile_name=profile)
    if region is not None:
        oci_config['region'] = region
    return oci_config


def get_core_client(region=None, profile='DEFAULT'):
    return oci.core.ComputeClient(get_oci_config(region, profile))


def get_net_client(region=None, profile='DEFAULT'):
    return oci.core.VirtualNetworkClient(get_oci_config(region, profile))


def get_search_client(region=None, profile='DEFAULT'):
    return oci.resource_search.ResourceSearchClient(
        get_oci_config(region, profile))


def get_identity_client(region=None, profile='DEFAULT'):
    return oci.identity.IdentityClient(get_oci_config(region, profile))


def service_exception():
    """OCI service exception."""
    return oci.exceptions.ServiceError
