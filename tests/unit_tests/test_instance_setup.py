"""Tests for sky.provision.instance_setup."""
import resource
import subprocess
from typing import Tuple

import pytest

from sky.provision import instance_setup

# pylint: disable=protected-access
_TARGET = instance_setup._TARGET_NOFILE


def _process_hard_nofile_limit() -> int:
    """This process' hard nofile limit, or -1 if it is unlimited."""
    return resource.getrlimit(resource.RLIMIT_NOFILE)[1]


def _raise_nofile_limit(soft: int, hard: int) -> Tuple[int, int]:
    """Runs RAISE_NOFILE_LIMIT_CMD under the given limits.

    The limits are lowered by the shell itself (a process may always lower its
    own limits), so no privileges are needed. Returns the resulting (soft,
    hard) limits.
    """
    # Soft first: lowering the hard limit below the current soft limit is
    # rejected with EINVAL.
    script = (f'ulimit -Sn {soft} && ulimit -Hn {hard} && '
              f'{instance_setup.RAISE_NOFILE_LIMIT_CMD}; '
              'echo "$(ulimit -Sn) $(ulimit -Hn)"')
    out = subprocess.run(['bash', '-c', script],
                         capture_output=True,
                         text=True,
                         check=True).stdout.split()
    return int(out[0]), int(out[1])


def test_raise_nofile_limit_clamps_to_hard_limit():
    """Below the target, the soft limit is raised as far as it can go."""
    process_hard = _process_hard_nofile_limit()
    if 0 <= process_hard < 2048:
        pytest.skip(f'hard nofile limit of this process is {process_hard}')
    assert _raise_nofile_limit(soft=1024, hard=2048) == (2048, 2048)


def test_raise_nofile_limit_reaches_target():
    """The target is reached when the hard limit allows it."""
    process_hard = _process_hard_nofile_limit()
    if 0 <= process_hard < _TARGET:
        pytest.skip(f'hard nofile limit of this process is {process_hard}, '
                    f'below the target {_TARGET}')
    # The hard limit must be left untouched: `ulimit -Sn` only sets the soft
    # limit, so a hard limit above the target is not lowered to it.
    hard = _TARGET * 2 if process_hard < 0 else process_hard
    assert _raise_nofile_limit(soft=1024, hard=hard) == (_TARGET, hard)
