import logging
from pathlib import Path

RETRIES = 30
MSI_NAME = "ray-msi-user-identity"
NSG_NAME = "ray-nsg"
SUBNET_NAME = "ray-subnet"
VNET_NAME = "ray-vnet"

logger = logging.getLogger(__name__)


def bootstrap_lambda(config):
    config = _configure_key_pair(config)
    return config


def _configure_key_pair(config):
    return config
