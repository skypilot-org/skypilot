"""Spheron configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
    region: str, cluster_name: str, config: common.ProvisionConfig
) -> common.ProvisionConfig:
    """Nothing to bootstrap.

    Spheron has no VPC, subnet, security group or firewall to prepare: an
    offer is deployed as-is and the instance comes up with a public address.
    """
    del region, cluster_name  # unused
    return config
