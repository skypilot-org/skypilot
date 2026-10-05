"""Latitude.sh configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
    region: str, cluster_name: str, config: common.ProvisionConfig
) -> common.ProvisionConfig:
    """Nothing to bootstrap.

    Latitude.sh has no VPC, subnet, security group or firewall to prepare:
    a server deploys as-is with a public management IPv4 and direct SSH on
    port 22 (the optional firewall product is not used by this lane).
    """
    del region, cluster_name  # unused
    return config
