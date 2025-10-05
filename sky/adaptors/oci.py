"""Oracle OCI cloud adaptor"""

import functools
import logging
import os

from sky.adaptors import common
from sky.clouds.utils import oci_utils

# Suppress OCI circuit breaker logging before lazy import, because
# oci modules prints additional message during imports, i.e., the
# set_logger in the LazyImport called after imports will not take
# effect.
logging.getLogger('oci.circuit_breaker').setLevel(logging.WARNING)

OCI_CONFIG_PATH = '~/.oci/config'
ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'

oci = common.LazyImport(
    'oci',
    import_error_message='Failed to import dependencies for OCI. '
    'Try running: pip install "skypilot[oci]"')


def get_config_file() -> str:
    conf_file_path = OCI_CONFIG_PATH
    config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
    if config_path_via_env_var is not None:
        conf_file_path = config_path_via_env_var
    return conf_file_path


def get_oci_config(region=None, profile='DEFAULT'):
    conf_file_path = get_config_file()
    if not profile or profile == 'DEFAULT':
        config_profile = oci_utils.oci_config.get_profile()
    else:
        config_profile = profile

    oci_config = oci.config.from_file(file_location=conf_file_path,
                                      profile_name=config_profile)
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


def get_object_storage_client(region=None, profile='DEFAULT'):
    return oci.object_storage.ObjectStorageClient(
        get_oci_config(region, profile))


def service_exception():
    """OCI service exception."""
    return oci.exceptions.ServiceError


def with_oci_env(f):

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        # pylint: disable=line-too-long
        enter_env_cmds = [
            'conda info --envs | grep "sky-oci-cli-env" || conda create -n sky-oci-cli-env python=3.10 -y',
            '. $(conda info --base 2> /dev/null)/etc/profile.d/conda.sh > /dev/null 2>&1 || true',
            'conda activate sky-oci-cli-env', 'pip install oci-cli',
            'export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True'
        ]
        operation_cmd = [f(*args, **kwargs)]
        leave_env_cmds = ['conda deactivate']
        return ' && '.join(enter_env_cmds + operation_cmd + leave_env_cmds)

    return wrapper
