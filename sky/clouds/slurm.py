"""Kubernetes."""
import subprocess
import typing
from typing import Dict, Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky.utils import registry

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)


@registry.CLOUD_REGISTRY.register()
class Slurm(clouds.Cloud):
    """Slurm."""
    _REPR = 'Slurm'

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to Slurm."""
        try:
            proc = subprocess.run(['sinfo'],
                                  stderr=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  check=False,
                                  timeout=2)
            if proc.returncode != 0:
                return (False, 'Slurm is not configured. To check, run: sinfo')
            return (True, None)
        except FileNotFoundError:
            return (False, 'Slurm command not found. To check, run: sinfo')

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources  # Unused.
        return {
            feature: f'{cls._REPR} only supports show-gpus for now.'
            for feature in clouds.CloudImplementationFeatures
        }

    def get_credential_file_mounts(self) -> registry.Dict[str, str]:
        return {}
