"""Hyperbolic Cloud configuration bootstrapping"""

from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    del region, cluster_name  # unused
    return config
