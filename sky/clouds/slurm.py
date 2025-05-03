"""Kubernetes."""
import subprocess
from typing import Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky.utils import registry

logger = sky_logging.init_logger(__name__)


@registry.CLOUD_REGISTRY.register()
class Slurm(clouds.Cloud):
    """Slurm."""
    _REPR = 'Slurm'

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to Slurm."""
        proc = subprocess.run(['sinfo'],
                              stderr=subprocess.PIPE,
                              stdout=subprocess.PIPE,
                              check=False)
        if proc.returncode != 0:
            return (False, 'Slurm is not configured. To check, run: sinfo')
        return (True, None)

    def get_credential_file_mounts(self) -> registry.Dict[str, str]:
        return {}
