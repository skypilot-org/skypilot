"""QuantaCloud configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Nothing to bootstrap.

    QuantaCloud has no VPC, subnet, security group or firewall to prepare:
    a deployment boots as-is with a public IP and direct SSH on port 22
    (the stock image ships NVIDIA drivers + Docker already).
    """
    del region, cluster_name  # unused
    return config
