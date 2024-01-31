"""FluidStack configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    del region, cluster_name  # unused

    return config


def bootstrap_config(cluster_config):
    """Bootstraps cluster configuration."""
    return cluster_config


def fillout_available_node_types_resources(cluster_config):
    """Fills out available node types' resources."""
    return cluster_config
