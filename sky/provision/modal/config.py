"""Modal configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """No-op: no pre-launch resource setup needed for Modal."""
    del region, cluster_name  # unused
    return config
