"""Configuration for service catalog."""

import contextlib
import threading

from sky.utils import common_utils

local_config = threading.local()
# Whether the caller requires the catalog to be narrowed down
# to the account-specific catalog or just the raw catalog
# fetched from SkyPilot catalog service.
local_config.use_default_catalog = False


@contextlib.contextmanager
def set_use_default_catalog(name: str, to_use: bool):
    del name  # Unused
    old_value = local_config.use_default_catalog
    local_config.use_default_catalog = to_use
    try:
        yield
    finally:
        local_config.use_default_catalog = old_value


def get_use_default_catalog() -> bool:
    return local_config.use_default_catalog


# Decorator to disable account-specific catalog.
def use_default_catalog(func):
    return common_utils.make_decorator(set_use_default_catalog,
                                       func,
                                       to_use=True)
