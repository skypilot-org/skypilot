"""Vsphere configuration bootstrapping."""

from sky.provision import common
from sky.utils import sky_logging

logger = sky_logging.init_logger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
    logger.info(f'New provision of Vsphere: bootstrap_instances().Region: '
                f'{region} Cluster Name:{cluster_name}')

    # TODO: process config.

    return config
