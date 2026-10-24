"""io.net configuration bootstrapping."""

from sky.provision import common


def bootstrap_instances(
        region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    del region, cluster_name_on_cloud
    return config
