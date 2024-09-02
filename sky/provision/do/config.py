"""Paperspace configuration bootstrapping."""

from sky import sky_logging
from sky.provision import common

logger = sky_logging.init_logger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    del region, cluster_name
    return config
