"""Utility functions for cluster yaml file."""

import os
import re

from sky.utils import common_utils

SKY_CLUSTER_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'


def get_cluster_yaml_absolute_path() -> str:
    """Return the absolute path of the cluster yaml file.

    This function should be called on the remote machine.
    """
    return os.path.abspath(os.path.expanduser(SKY_CLUSTER_YAML_REMOTE_PATH))


def get_provider_name() -> str:
    """Return the name of the provider."""
    config = common_utils.read_yaml(get_cluster_yaml_absolute_path())

    provider_module = config['provider']['module']
    # Examples:
    #   'sky.skylet.providers.aws.AWSNodeProviderV2' -> 'aws'
    #   'sky.provision.aws' -> 'aws'
    provider_search = re.search(r'(?:providers|provision)\.(\w+)\.?',
                                provider_module)
    assert provider_search is not None, config
    provider_name = provider_search.group(1).lower()
    return provider_name
