"""Kubernetes."""
import os
import re
import typing
from typing import Dict, Iterator, List, Optional, Set, Tuple, Union

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.clouds import service_catalog
from sky.provision import instance_setup
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)


@registry.CLOUD_REGISTRY.register()
class Slurm(clouds.Cloud):
    """Slurm."""
    _REPR = 'Slurm'

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to Slurm."""
        return (True, None)
