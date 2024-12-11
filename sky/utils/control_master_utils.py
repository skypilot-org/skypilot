"""Utils to check if the ssh control master should be disabled."""

import functools
import subprocess

from sky import sky_logging
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# The maximum number of concurrent ssh connections to a same node. This is a
# heuristic value, based on the observation that when the number of concurrent
# ssh connections to a node with control master is high, new connections through
# control master will hang.
_MAX_CONCURRENT_SSH_CONNECTIONS = 32


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


def should_disable_control_master(ip: str) -> bool:
    """Whether disable ssh control master based on file system.

    Args:
        ip: The ip address of the node.

    Returns:
        bool: True if the ssh control master should be disabled,
        False otherwise.
    """
    if is_unsupported_filesystem():
        return True
    if is_high_concurrency(ip):
        return True
    # there may be additional criteria to disable ssh control master
    # in the future. They should be checked here
    return False


@functools.lru_cache(maxsize=1)
def is_unsupported_filesystem():
    """Determine if the filesystem is unsupported."""
    return is_tmp_9p_filesystem()


def is_high_concurrency(ip: str) -> bool:
    """Determine if the node has high concurrent ssh connections.

    Args:
        ip: The IP address to check
        threshold: Maximum number of allowed concurrent SSH connections

    Returns:
        bool: True if number of concurrent SSH connections exceeds threshold
    """
    try:
        # Use pgrep to efficiently find ssh processes and pipe to grep for IP
        cmd = f'pgrep -f ssh | xargs -r ps -p | grep -c {ip}'
        proc = subprocess.run(cmd,
                              shell=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True,
                              check=False)
        if proc.returncode == 0:
            count = int(proc.stdout.strip())
            return count >= _MAX_CONCURRENT_SSH_CONNECTIONS
        return False
    except (subprocess.SubprocessError, ValueError):
        return False
