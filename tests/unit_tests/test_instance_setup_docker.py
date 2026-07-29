"""Docker-backed tests for the nofile limit commands in instance_setup.

A container buys two things the host cannot provide itself:

* a hard nofile limit above the host's own (or unlimited) -- raising a hard
  limit needs CAP_SYS_RESOURCE, which a test process does not have;
* a private pid namespace, so the ``pgrep -f raylet/raylet`` inside
  ``RAY_PRLIMIT`` matches only this test's stand-in raylet rather than an
  unrelated process on the machine.

The command strings are imported from ``instance_setup`` and run unmodified,
against two stand-in raylets -- one forked before the raise and one after -- so
that both the inheritance the Kubernetes entrypoint relies on and the prlimit
backstop for a raylet that missed it are covered. See ``_SCRIPT``.

The clamp semantics that need neither capability live in
``test_instance_setup.py`` and run everywhere. These skip when there is no
usable daemon, or when the daemon cannot grant the scenario's hard limit (a
nested daemon inherits a low RLIMIT_NOFILE and cannot raise a container above
it).
"""
import functools
import os
import shutil
import subprocess
from typing import Dict, List, Tuple

import pytest

from sky.provision import instance_setup

# pylint: disable=protected-access
_TARGET = instance_setup._TARGET_NOFILE

_IMAGE = os.environ.get('SKYPILOT_TEST_NOFILE_IMAGE', 'ubuntu:22.04')
_DOCKER = shutil.which('docker')
# Generous: the first run may pull the image.
_TIMEOUT = 300

# Run inside the container, fed over stdin so that the container's own command
# line does not contain "raylet/raylet" and confuse pgrep. `sudo` is shimmed
# because RAY_PRLIMIT calls `sudo prlimit` and the image has no sudo package;
# the container is root, which is also what the K8s template assumes (it
# defines an equivalent no-op sudo shell function).
#
# Two stand-in raylets are forked, on purpose:
#
# * STALE, before the raise, so it keeps the container's default soft limit.
#   RAY_PRLIMIT's effect is only observable on such a process -- it is the
#   backstop for a raylet that did not inherit the raise (every non-Kubernetes
#   template, where nothing raises the limit before ray starts).
# * FRESH, after the raise, which is what the pod entrypoint actually produces.
#   Its limit must already be raised *before* RAY_PRLIMIT runs.
#
# Asserting only on FRESH would make the RAY_PRLIMIT assertions vacuous: it is
# already at the maximum, so a prlimit that silently does nothing still passes.
_SCRIPT = f"""
set +e
printf '#!/bin/sh\\nexec "$@"\\n' > /usr/local/bin/sudo && chmod +x /usr/local/bin/sudo
limits() {{ awk '/Max open files/ {{print $4, $5}}' /proc/$1/limits; }}
echo "BEFORE=$(ulimit -Sn) $(ulimit -Hn)"
exec -a raylet/raylet sleep 300 &
STALE=$!
{instance_setup.RAISE_NOFILE_LIMIT_CMD}
echo "SHELL=$(ulimit -Sn) $(ulimit -Hn)"
exec -a raylet/raylet sleep 300 &
FRESH=$!
sleep 0.5
echo "STALE_BEFORE=$(limits $STALE)"
echo "FRESH_BEFORE=$(limits $FRESH)"
{instance_setup.RAY_PRLIMIT}
echo "PRLIMIT_RC=$?"
echo "STALE_AFTER=$(limits $STALE)"
echo "FRESH_AFTER=$(limits $FRESH)"
kill $STALE $FRESH 2>/dev/null
"""


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([_DOCKER, *args],
                          capture_output=True,
                          text=True,
                          timeout=_TIMEOUT,
                          check=False)


@functools.lru_cache(maxsize=1)
def _daemon_reachable() -> bool:
    try:
        return _docker('info').returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _run(flags: List[str]) -> Dict[str, str]:
    """Runs _SCRIPT in a container, or skips if the flags are not grantable."""
    if not _daemon_reachable():
        pytest.skip('docker daemon not reachable')
    # Same flags as the real run, so a failure here is a reliable skip signal:
    # a daemon with a low RLIMIT_NOFILE cannot grant a high container limit.
    if _docker('run', '--rm', *flags, _IMAGE, 'true').returncode != 0:
        pytest.skip(f'daemon cannot grant {flags}')
    proc = subprocess.run(
        [_DOCKER, 'run', '--rm', '-i', *flags, _IMAGE, 'bash', '-s'],
        input=_SCRIPT,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False)
    assert proc.returncode == 0, (f'container exited {proc.returncode}\n'
                                  f'stdout:\n{proc.stdout}\n'
                                  f'stderr:\n{proc.stderr}')
    parsed = dict(
        line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
    for key in ('BEFORE', 'SHELL', 'STALE_BEFORE', 'FRESH_BEFORE', 'PRLIMIT_RC',
                'STALE_AFTER', 'FRESH_AFTER'):
        assert key in parsed, f'missing {key} in:\n{proc.stdout}'
    return parsed


def _pair(value: str) -> Tuple[str, str]:
    soft, hard = value.split()
    return soft, hard


def _clamped(hard: str) -> str:
    """min(_TARGET, hard), as the string the shell prints."""
    if hard == 'unlimited' or int(hard) > _TARGET:
        return str(_TARGET)
    return hard


# (id, hard limit for `docker run --ulimit`, add CAP_SYS_RESOURCE)
_SCENARIOS = [
    # The reported Kubernetes case: the raise to _TARGET is not permitted, so
    # the soft limit must clamp up to the hard limit instead of staying low.
    ('hard_below_target', '524288', False),
    # A hard limit above _TARGET must survive: `ulimit -Sn` sets only the soft
    # limit, unlike the plain `ulimit -n` this replaced.
    ('hard_above_target', '2000000', False),
    ('hard_unlimited', '-1', False),
    # The VM path: with the capability, prlimit's first attempt raises both
    # limits of the raylet to _TARGET even from a tiny starting limit.
    ('with_cap_sys_resource', '1024', True),
]


def _assert_clamp(result: Dict[str, str], cap: bool) -> None:
    container_soft, container_hard = _pair(result['BEFORE'])
    clamped = _clamped(container_hard)
    expected_shell = (clamped, container_hard)

    # RAISE_NOFILE_LIMIT_CMD: the soft limit is raised as far as it can go, and
    # the hard limit is left untouched.
    assert _pair(result['SHELL']) == expected_shell
    # A raylet forked after the raise inherits it -- what the pod entrypoint
    # relies on, since ray is started by one of the STEPS it forks.
    assert _pair(result['FRESH_BEFORE']) == expected_shell
    # ...and one forked before it does not, which is what makes the RAY_PRLIMIT
    # assertions below meaningful.
    assert _pair(result['STALE_BEFORE']) == (container_soft, container_hard)

    # RAY_PRLIMIT: raises the stale raylet to the same clamp, or to _TARGET
    # outright when the capability to raise its hard limit is available.
    assert result['PRLIMIT_RC'] == '0'
    expected_raylet = ((str(_TARGET), str(_TARGET)) if cap else
                       (clamped, clamped))
    assert _pair(result['STALE_AFTER']) == expected_raylet
    assert _pair(result['FRESH_AFTER']) == expected_raylet


@pytest.mark.skipif(_DOCKER is None, reason='docker CLI not found on PATH')
@pytest.mark.parametrize('hard,cap', [s[1:] for s in _SCENARIOS],
                         ids=[s[0] for s in _SCENARIOS])
def test_nofile_limit_clamp(hard: str, cap: bool):
    flags = [f'--ulimit=nofile=1024:{hard}']
    if cap:
        flags.append('--cap-add=SYS_RESOURCE')
    _assert_clamp(_run(flags), cap)


@functools.lru_cache(maxsize=1)
def _max_grantable_hard() -> str:
    """Highest hard nofile limit this daemon can give a container, '' if none."""
    for candidate in (str(_TARGET), '65536', '4096', '1024'):
        probe = _docker('run', '--rm', f'--ulimit=nofile=1024:{candidate}',
                        _IMAGE, 'true')
        if probe.returncode == 0:
            return candidate
    return ''


@pytest.mark.skipif(_DOCKER is None, reason='docker CLI not found on PATH')
def test_nofile_limit_clamp_at_daemon_ceiling():
    """Exercises the clamp live even where the fixed scenarios above skip.

    A nested or sandboxed daemon inherits a low RLIMIT_NOFILE and cannot grant
    a container more, which would leave every scenario above skipped and the
    container path untested. Run at whatever the daemon can actually give.
    """
    if not _daemon_reachable():
        pytest.skip('docker daemon not reachable')
    hard = _max_grantable_hard()
    if not hard:
        pytest.skip('daemon rejected every probed --ulimit nofile value')
    _assert_clamp(_run([f'--ulimit=nofile=1024:{hard}']), cap=False)
