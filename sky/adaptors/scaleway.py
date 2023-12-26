"""Scaleway cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools

from sky import sky_logging

CREDENTIAL_FILE = '~/.config/scw/config.yaml'
logger = sky_logging.init_logger(__name__)

scaleway = None
scaleway_instance = None
scaleway_client = None


def import_package(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global scaleway
        if scaleway is None:
            try:
                import scaleway as _scaleway  # type: ignore
                scaleway = _scaleway
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for Scaleway.'
                    'Try pip install "skypilot[scaleway]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_instance():
    from scaleway.instance.v1 import InstanceV1API
    return InstanceV1API


@import_package
def get_marketplace():
    from scaleway.marketplace.v2 import MarketplaceV2API
    return MarketplaceV2API


@import_package
def get_client():
    return scaleway.Client.from_config_file_and_env()
