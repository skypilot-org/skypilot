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
        try:
            proc = subprocess.run(['sinfo'],
                                stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                check=False, timeout=2)
            if proc.returncode != 0:
                return (False, 'Slurm is not configured. To check, run: sinfo')
            return (True, None)
        except FileNotFoundError:
            return (False, 'Slurm command not found. To check, run: sinfo')

    def get_credential_file_mounts(self) -> registry.Dict[str, str]:
        return {}
