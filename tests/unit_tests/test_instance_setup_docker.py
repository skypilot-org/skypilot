"""Docker-backed tests for the nofile limit commands in instance_setup.

A container buys two things the host cannot provide itself:

* a hard nofile limit different from the host's own -- raising a hard limit
  needs CAP_SYS_RESOURCE, which a test process does not have;
* a private pid namespace, so the ``pgrep -f raylet/raylet`` inside
  ``RAY_PRLIMIT`` matches only this test's stand-in raylet rather than an
  unrelated process on the machine.

The command strings are imported from ``instance_setup`` and run VERBATIM --
never rewritten or scaled -- against two stand-in raylets, one forked before
the raise and one after, so that both the inheritance the Kubernetes
entrypoint relies on and the prlimit backstop for a raylet that missed it are
covered. See ``_SCRIPT``.

The kernel caps RLIMIT_NOFILE's hard limit at ``fs.nr_open``, whose default is
exactly _TARGET_NOFILE (1048576). Consequences for the scenarios:

* a hard limit *above* the target cannot be granted by ``--ulimit`` on a stock
  kernel. That scenario bootstraps it honestly instead: a ``--privileged``
  container raises the daemon kernel's fs.nr_open, sets its own hard limit
  above the target, then drops every capability with ``setpriv`` before
  running the real commands (restoring fs.nr_open afterwards);
* an *unlimited* hard nofile limit is impossible on Linux (RLIM_INFINITY
  always exceeds fs.nr_open), so there is no such scenario.

An unusable daemon (or no docker at all) FAILS these tests in CI -- a skip
there would silently drop the only coverage of the container path. On a dev
machine without docker it degrades to a skip.
"""
import functools
import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

import pytest

from sky.provision import instance_setup

# pylint: disable=protected-access
_TARGET = instance_setup._TARGET_NOFILE

_IMAGE = os.environ.get('SKYPILOT_TEST_NOFILE_IMAGE', 'ubuntu:22.04')
_DOCKER = shutil.which('docker')
# Generous: the first run may pull the image.
_TIMEOUT = 300

# The reported production shape: 1024:524288 is a common container default.
_HARD_BELOW_TARGET = 524288
_HARD_ABOVE_TARGET = 2 * _TARGET

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


def _unavailable(reason: str) -> None:
    """Docker cannot run this scenario: fail in CI, skip on a dev machine."""
    if os.environ.get('CI'):
        pytest.fail(f'{reason} -- docker is required in CI')
    pytest.skip(reason)


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


def _require_daemon() -> None:
    if _DOCKER is None:
        _unavailable('docker CLI not found on PATH')
    if not _daemon_reachable():
        _unavailable('docker daemon not reachable')


def _run(flags: List[str], argv: Optional[List[str]] = None) -> Dict[str, str]:
    """Feeds _SCRIPT over stdin to `docker run <flags> <argv>`.

    argv defaults to `bash -s`, which reads _SCRIPT directly; a scenario may
    interpose a bootstrap that ends by exec'ing `bash -s` itself.
    """
    _require_daemon()
    if _docker('run', '--rm', *flags, _IMAGE, 'true').returncode != 0:
        _unavailable(f'daemon cannot run a container with {flags}')
    proc = subprocess.run([
        _DOCKER, 'run', '--rm', '-i', *flags, _IMAGE, *(argv or ['bash', '-s'])
    ],
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


def _assert_clamp(result: Dict[str, str], start: Tuple[int, int],
                  cap: bool) -> None:
    # The starting limits are asserted, not just echoed: a scenario whose
    # bootstrap silently failed to produce its intended limits would otherwise
    # pass as a different (already-covered) scenario.
    container_soft, container_hard = _pair(result['BEFORE'])
    assert (container_soft, container_hard) == (str(start[0]), str(start[1]))
    clamped = str(min(_TARGET, start[1]))
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

    # RAY_PRLIMIT: sets the raylet to _TARGET:_TARGET whenever that is
    # permitted -- with the capability, or by *lowering* a higher hard limit,
    # which needs none -- and otherwise clamps the soft limit up to the
    # raylet's own hard limit.
    assert result['PRLIMIT_RC'] == '0'
    reaches_target = cap or start[1] >= _TARGET
    expected_raylet = ((str(_TARGET), str(_TARGET)) if reaches_target else
                       (clamped, clamped))
    assert _pair(result['STALE_AFTER']) == expected_raylet
    assert _pair(result['FRESH_AFTER']) == expected_raylet


def test_nofile_limit_clamp_hard_below_target():
    """The reported Kubernetes case: the raise to _TARGET is not permitted, so
    the soft limit must clamp up to the hard limit instead of staying low."""
    result = _run([f'--ulimit=nofile=1024:{_HARD_BELOW_TARGET}'])
    _assert_clamp(result, start=(1024, _HARD_BELOW_TARGET), cap=False)


def test_nofile_limit_clamp_hard_above_target():
    """A hard limit above _TARGET must survive the shell raise: `ulimit -Sn`
    sets only the soft limit, unlike the plain `ulimit -n` this replaced.

    `--ulimit` cannot grant a hard limit above the daemon kernel's fs.nr_open
    (= _TARGET on a stock kernel), so the container bootstraps it: raise
    fs.nr_open (privileged, restored afterwards), raise its own hard limit,
    then drop every capability with setpriv before running the real commands
    -- the capability-less environment the commands see in production.
    """
    _require_daemon()
    orig = _docker('run', '--rm', _IMAGE, 'cat', '/proc/sys/fs/nr_open')
    assert orig.returncode == 0, orig.stderr
    orig_nr_open = int(orig.stdout)
    bootstrap = (f'echo {_HARD_ABOVE_TARGET} > /proc/sys/fs/nr_open && '
                 f'prlimit --nofile=1024:{_HARD_ABOVE_TARGET} --pid $$ && '
                 'exec setpriv --bounding-set -all bash -s')
    try:
        result = _run(['--privileged'], argv=['bash', '-c', bootstrap])
    finally:
        if orig_nr_open < _HARD_ABOVE_TARGET:
            # fs.nr_open belongs to the daemon's kernel, not the container;
            # put it back so the test leaves no trace on the machine.
            _docker('run', '--rm', '--privileged', _IMAGE, 'bash', '-c',
                    f'echo {orig_nr_open} > /proc/sys/fs/nr_open')
    _assert_clamp(result, start=(1024, _HARD_ABOVE_TARGET), cap=False)


def test_nofile_limit_clamp_with_cap_sys_resource():
    """The VM path: with the capability, prlimit's first attempt raises both
    limits of the raylet to _TARGET even from a tiny starting limit."""
    result = _run(['--ulimit=nofile=1024:1024', '--cap-add=SYS_RESOURCE'])
    _assert_clamp(result, start=(1024, 1024), cap=True)
