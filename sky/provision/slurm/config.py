"""Slrum-specific configuration for the provisioner."""
import logging

from sky.provision import common

logger = logging.getLogger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    logger.critical(f"[BOOTSTRAP] {region} {cluster_name}")
    return config
