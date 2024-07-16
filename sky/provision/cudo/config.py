"""Cudo Compute configuration bootstrapping."""

from sky.provision import common
from sky.provision.cudo import cudo_wrapper


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    cudo_wrapper.setup_network(region, cluster_name)
    return config
