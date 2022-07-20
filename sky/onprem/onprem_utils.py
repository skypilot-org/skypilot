"""Util constants/functions for Sky Onprem."""
import os
from typing import List

import yaml

from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def check_if_local_cloud(cluster: str) -> bool:
    """Checks if cluster name is a local cloud.

    If cluster is a public cloud, this function will not check local
    cluster configs. If this cluster is a private cloud, this function
    will run correctness tests for cluster configs.
    """
    config_path = os.path.expanduser(
        backend_utils.SKY_USER_LOCAL_CONFIG_PATH.format(cluster))
    if not os.path.exists(config_path):
        # Public clouds go through no error checking.
        return False
    # Go through local cluster check to raise potential errors.
    check_and_get_local_clusters(suppress_error=False)
    return True


def check_and_get_local_clusters(suppress_error: bool = False) -> List[str]:
    """Lists all local clusters and checks cluster config validity.

    Args:
        suppress_error: Whether to suppress any errors raised.
    """
    local_dir = os.path.expanduser(
        os.path.dirname(backend_utils.SKY_USER_LOCAL_CONFIG_PATH))
    os.makedirs(local_dir, exist_ok=True)
    local_cluster_paths = [
        os.path.join(local_dir, f) for f in os.listdir(local_dir)
    ]
    # Filter out folders.
    local_cluster_paths = [
        path for path in local_cluster_paths
        if os.path.isfile(path) and path.endswith('.yml')
    ]

    local_cluster_names = []
    name_to_path_dict = {}
    for path in local_cluster_paths:
        # TODO(mluo): Define a scheme for cluster config to check if YAML
        # schema is correct.
        with open(path, 'r') as f:
            yaml_config = yaml.safe_load(f)
            user_config = yaml_config['auth']
            cluster_name = yaml_config['cluster']['name']
        sky_local_path = backend_utils.SKY_USER_LOCAL_CONFIG_PATH
        if (backend_utils.AUTH_PLACEHOLDER
                in (user_config['ssh_user'], user_config['ssh_private_key']) and
                not suppress_error):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Authentication into local cluster requires '
                                 'specifying `ssh_user` and `ssh_private_key` '
                                 'under the `auth` dictionary. Please fill '
                                 'aforementioned fields in '
                                 f'{sky_local_path.format(cluster_name)}.')
        if cluster_name in local_cluster_names:
            if not suppress_error:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Multiple configs in ~/.sky/local/ have the same '
                        f'cluster name {cluster_name!r}. '
                        'Fix the duplication and retry:'
                        f'\nCurrent config: {path}'
                        f'\nExisting config: {name_to_path_dict[cluster_name]}')
        else:
            name_to_path_dict[cluster_name] = path
            local_cluster_names.append(cluster_name)

    # Remove clusters that are in global user state but are not in
    # ~/.sky/local.
    records = backend_utils.get_clusters(
        include_reserved=False,
        refresh=False,
        cloud_filter=backend_utils.CloudFilter.LOCAL)
    saved_clusters = [r['name'] for r in records]
    for cluster_name in saved_clusters:
        if cluster_name not in local_cluster_names:
            logger.warning(f'Removing local cluster {cluster_name} from '
                           '`sky status`. No config found in ~/.sky/local.')
            global_user_state.remove_cluster(cluster_name, terminate=True)

    return local_cluster_names
