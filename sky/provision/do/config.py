"""Paperspace configuration bootstrapping."""

from sky import sky_logging
from sky.provision import common
from sky.provision.paperspace import utils

logger = sky_logging.init_logger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstraps instances for the given cluster."""
    # FIX (asaiacai): could provision VPC per cluster but stick with
    # default VPC for now

    # Add pubkey to machines via startup
    public_key = config.authentication_config['ssh_public_key']
    client.set_sky_key_script(public_key)

    return config
