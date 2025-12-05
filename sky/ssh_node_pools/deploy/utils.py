"""Utilities for SSH Node Pools Deployment"""

import subprocess
from typing import List, Optional

from sky.utils import ux_utils


def check_ssh_cluster_dependencies(
        raise_error: bool = True) -> Optional[List[str]]:
    """Checks if the dependencies for ssh cluster are installed.

    Args:
        raise_error: set to true when the dependency needs to be present.
            set to false for `sky check`, where reason strings are compiled
            at the end.

    Returns: the reasons list if there are missing dependencies.
    """
    # error message
    jq_message = ('`jq` is required to setup ssh cluster.')

    # save
    reasons = []
    required_binaries = []

    # Ensure jq is installed
    try:
        subprocess.run(['jq', '--version'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        required_binaries.append('jq')
        reasons.append(jq_message)

    if required_binaries:
        reasons.extend([
            'On Debian/Ubuntu, install the missing dependenc(ies) with:',
            f'  $ sudo apt install {" ".join(required_binaries)}',
            'On MacOS, install with: ',
            f'  $ brew install {" ".join(required_binaries)}',
        ])
        if raise_error:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('\n'.join(reasons))
        return reasons
    return None
