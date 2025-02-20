"""Utils to check if the ssh control master should be disabled."""

from sky import sky_logging
from sky.utils import annotations
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)


def is_tmp_9p_filesystem() -> bool:
    """Check if the /tmp filesystem is 9p.

    Returns:
        bool: True if the /tmp filesystem is 9p, False otherwise.
    """

    result = subprocess_utils.run(['df', '-T', '/tmp'],
                                  capture_output=True,
                                  text=True,
                                  shell=None,
                                  check=False,
                                  executable=None)

    if result.returncode != 0:
        return False

    filesystem_infos = result.stdout.strip().split('\n')
    if len(filesystem_infos) < 2:
        return False
    filesystem_types = filesystem_infos[1].split()
    if len(filesystem_types) < 2:
        return False
    return filesystem_types[1].lower() == '9p'


@annotations.lru_cache(scope='global')
def should_disable_control_master() -> bool:
    """Whether disable ssh control master based on file system.

    Returns:
        bool: True if the ssh control master should be disabled,
        False otherwise.
    """
    if is_tmp_9p_filesystem():
        return True
    # there may be additional criteria to disable ssh control master
    # in the future. They should be checked here
    return False
