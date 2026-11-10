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


def _skip_if_hard_limit_below(hard: int) -> None:
    process_hard = _process_hard_nofile_limit()
    if 0 <= process_hard < hard:
        pytest.skip(f'hard nofile limit of this process is {process_hard}, '
                    f'below the {hard} this test needs')


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
    _skip_if_hard_limit_below(2048)
    assert _raise_nofile_limit(soft=1024, hard=2048) == (2048, 2048)


def test_raise_nofile_limit_reaches_target():
    """The real target is reached when the host's hard limit allows it."""
    _skip_if_hard_limit_below(_TARGET)
    process_hard = _process_hard_nofile_limit()
    hard = _TARGET * 2 if process_hard < 0 else process_hard
    assert _raise_nofile_limit(soft=1024, hard=hard) == (_TARGET, hard)
