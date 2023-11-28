"""Utility functions for cluster yaml file on remote cluster.

This module should only be used on the remote cluster.
"""

import os
import re

from sky.utils import common_utils

# The cluster yaml used to create the current cluster where the module is
# called.
SKY_CLUSTER_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'


def get_cluster_yaml_absolute_path() -> str:
    """Return the absolute path of the cluster yaml file."""
    return os.path.abspath(os.path.expanduser(SKY_CLUSTER_YAML_REMOTE_PATH))


def load_cluster_yaml() -> dict:
    """Load the cluster yaml file."""
    return common_utils.read_yaml(get_cluster_yaml_absolute_path())


def get_provider_name(config: dict) -> str:
    """Return the name of the provider."""

    provider_module = config['provider']['module']
    # Examples:
    #   'sky.skylet.providers.aws.AWSNodeProviderV2' -> 'aws'
    #   'sky.provision.aws' -> 'aws'
    provider_search = re.search(r'(?:providers|provision)\.(\w+)\.?',
                                provider_module)
    assert provider_search is not None, config
    provider_name = provider_search.group(1).lower()
    return provider_name
