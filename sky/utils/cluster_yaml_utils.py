"""Utility functions for cluster yaml file."""

import re

# The cluster yaml used to create the current cluster where the module is
# called.
SKY_CLUSTER_YAML_REMOTE_PATH = '~/.sky/sky_ray.yml'


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
    # Special handling for lambda_cloud as Lambda cloud is registered as lambda.
    if provider_name == 'lambda_cloud':
        provider_name = 'lambda'
    return provider_name
