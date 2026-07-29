"""Docker-backed tests for the open-files (nofile) limit clamp commands.

SkyPilot raises the open-files (nofile) limit for the Ray raylet at three
places, all sourced from ``sky.provision.instance_setup``:

* ``RAISE_NOFILE_SOFT_LIMIT_CMD`` -- a ``ulimit -Sn`` (soft-only) command that
  the Kubernetes/SSH pod template inlines at TWO sites (the pod entrypoint and
  the Ray ``setup_commands``).
* ``_RAY_PRLIMIT`` -- a ``prlimit`` command that raises the raylet process's
  soft limit toward 1048576, clamped to the raylet's *own* hard limit when it
  cannot raise the hard limit (no ``CAP_SYS_RESOURCE``).

These commands manipulate real kernel rlimits, so a mock cannot meaningfully
exercise them. This module runs the *actual* command strings imported from
source inside throwaway containers whose nofile limits are set with
``docker run --ulimit`` and reads back ``/proc/<pid>/limits`` to assert the
observed clamp behavior.

Binding to source
-----------------
The command strings are imported from ``instance_setup`` (never re-typed), and
``test_ulimit_constant_inlined_at_both_template_sites`` renders the real
``kubernetes-ray.yml.j2`` and asserts ``RAISE_NOFILE_SOFT_LIMIT_CMD`` appears
verbatim at both template sites. Editing either the template literal or the
constant without the other breaks the test -- catching silent drift.

Skipping
--------
The docker tests skip cleanly when the docker CLI is missing, when the daemon
is unreachable, and -- per scenario -- when the daemon cannot provide the
required hard nofile limit or ``CAP_SYS_RESOURCE`` (both common in nested/
sandboxed runners). ``test_ulimit_constant_inlined_at_both_template_sites``
needs no docker and always runs.

Image
-----
Defaults to ``ubuntu:22.04``, which ships ``bash``/``prlimit``/``pgrep``/
``awk`` so no in-container package install (and thus no container network) is
needed. A ``sudo`` shim is injected at runtime (the container runs as root, so
``sudo`` is a no-op wrapper) so the exact ``_RAY_PRLIMIT`` string -- which
calls ``sudo prlimit`` -- runs unmodified. Override with
``SKYPILOT_TEST_NOFILE_IMAGE`` for a different base image or registry mirror.
"""
import base64
import functools
import os
import shutil
import subprocess
from typing import Dict, List, Tuple

import pytest

from sky.provision.instance_setup import _RAY_PRLIMIT
from sky.provision.instance_setup import RAISE_NOFILE_SOFT_LIMIT_CMD

_TARGET_NOFILE = 1048576

_IMAGE = os.environ.get('SKYPILOT_TEST_NOFILE_IMAGE', 'ubuntu:22.04')
_DOCKER = shutil.which('docker')
# Per-container wall-clock budget (an image pull may happen on first run).
_RUN_TIMEOUT = 300


def _docker(*args: str,
            timeout: int = _RUN_TIMEOUT) -> subprocess.CompletedProcess:
    """Run ``docker`` with args, capturing output. No shell, no pipes."""
    return subprocess.run([_DOCKER, *args],
                          capture_output=True,
                          text=True,
                          timeout=timeout,
                          check=False)


@functools.lru_cache(maxsize=1)
def _daemon_reachable() -> bool:
    if _DOCKER is None:
        return False
    try:
        return _docker('info', timeout=60).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _run_flags_ok(flags: Tuple[str, ...]) -> bool:
    """True if ``docker run <flags> IMAGE true`` succeeds (exit 0)."""
    try:
        return _docker('run', '--rm', *flags, _IMAGE, 'true').returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@functools.lru_cache(maxsize=1)
def _max_hard_nofile() -> int:
    """Largest hard nofile limit this daemon can grant a container.

    A nested/sandboxed daemon inherits a low ``RLIMIT_NOFILE`` and cannot set a
    container's hard limit above its own, so probe descending candidates and
    return the first that a container accepts.
    """
    for candidate in (2000000, _TARGET_NOFILE, 524288, 65536, 4096, 1024):
        if _run_flags_ok((f'--ulimit=nofile=1024:{candidate}',)):
            return candidate
    return 0


@functools.lru_cache(maxsize=1)
def _cap_sys_resource_available() -> bool:
    return _run_flags_ok(('--cap-add=SYS_RESOURCE',))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


# Long-lived dummy process whose argv[0] matches the ``pgrep -f raylet/raylet``
# pattern in _RAY_PRLIMIT, standing in for the real raylet.
_SPAWN_RAYLET = 'exec -a raylet/raylet sleep 600'


def _container_script() -> str:
    """Bash script run inside the container.

    The real command strings are base64-decoded to files so this launcher's own
    argv does NOT contain ``raylet/raylet`` -- only the dummy process should
    match ``pgrep -f raylet/raylet``. Emits ``KEY=VALUE`` lines the test parses.
    """
    return f"""
set +e
export PATH=/usr/local/bin:$PATH
# sudo shim: the container runs as root, so sudo is a transparent wrapper. This
# lets the exact _RAY_PRLIMIT string (which calls `sudo prlimit`) run without
# the sudo package, keeping the container offline.
printf '#!/bin/sh\\nexec "$@"\\n' > /usr/local/bin/sudo
chmod +x /usr/local/bin/sudo
echo {_b64(RAISE_NOFILE_SOFT_LIMIT_CMD)} | base64 -d > /tmp/ulimit_cmd.sh
echo {_b64(_RAY_PRLIMIT)} | base64 -d > /tmp/prlimit_cmd.sh
echo {_b64(_SPAWN_RAYLET)} | base64 -d > /tmp/spawn_raylet.sh
lim() {{ awk '/Max open files/ {{print $4, $5}}' /proc/$1/limits; }}
# Spawn the dummy BEFORE touching the shell's own limits so it starts at the
# container defaults (its own hard limit is what _RAY_PRLIMIT must read back).
bash /tmp/spawn_raylet.sh & DPID=$!
sleep 0.5
echo "DUMMY_PID=$DPID"
echo "DUMMY_BEFORE=$(lim $DPID)"
# --- Site 1+2: RAISE_NOFILE_SOFT_LIMIT_CMD (both template sites use this) ---
# `source` so `ulimit -Sn` affects THIS shell (matches the entrypoint, where
# the command runs in the shell that forks the raylet).
source /tmp/ulimit_cmd.sh
echo "ULIMIT_AFTER=$(ulimit -Sn) $(ulimit -Hn)"
# --- Site 3: _RAY_PRLIMIT, acting on the dummy raylet by pid ---
bash /tmp/prlimit_cmd.sh
echo "PRLIMIT_EXIT=$?"
echo "DUMMY_AFTER=$(lim $DPID)"
kill $DPID 2>/dev/null || true
"""


def _parse_kv(stdout: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in stdout.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            out[key.strip()] = value.strip()
    return out


def _run_scenario(soft: int, hard: int, cap: bool) -> Dict[str, str]:
    flags: List[str] = [f'--ulimit=nofile={soft}:{hard}']
    if cap:
        flags.append('--cap-add=SYS_RESOURCE')
    proc = _docker('run', '--rm', *flags, _IMAGE, 'bash', '-c',
                   _container_script())
    assert proc.returncode == 0, (
        f'container exited {proc.returncode}\n'
        f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}')
    parsed = _parse_kv(proc.stdout)
    for key in ('ULIMIT_AFTER', 'PRLIMIT_EXIT', 'DUMMY_AFTER'):
        assert key in parsed, f'missing {key} in output:\n{proc.stdout}'
    return parsed


def _soft_hard(value: str) -> Tuple[int, int]:
    parts = value.split()
    assert len(parts) == 2, f'unexpected soft/hard value: {value!r}'
    return int(parts[0]), int(parts[1])


# ---------------------------------------------------------------------------
# Expected clamp behavior, derived from the command semantics and verified
# live (see the module's test run notes).
#
# RAISE_NOFILE_SOFT_LIMIT_CMD (ulimit -Sn 1048576, else ulimit -Sn $(-Hn)):
#   soft -> min(1048576, hard)   (raised to 1048576 if the hard limit allows,
#                                 otherwise clamped down to the hard limit)
#   hard -> unchanged            (`-Sn` never touches the hard limit)
#
# _RAY_PRLIMIT, without CAP_SYS_RESOURCE:
#   first tries `prlimit --nofile=1048576:1048576` (sets BOTH). Raising the hard
#   limit needs the cap; LOWERING or matching it does not.
#     hard >= 1048576: succeeds, soft=hard=1048576.
#     hard <  1048576: fails (would raise hard); fallback `--nofile=<hard>:`
#                      raises soft to the process's own hard, hard unchanged.
#   net: soft = hard = min(1048576, hard).
# _RAY_PRLIMIT, with CAP_SYS_RESOURCE:
#   first attempt succeeds (cap allows raising hard) -> soft=hard=1048576.
# ---------------------------------------------------------------------------
def _expected_ulimit(hard: int) -> Tuple[int, int]:
    return min(_TARGET_NOFILE, hard), hard


def _expected_prlimit(hard: int, cap: bool) -> Tuple[int, int]:
    if cap:
        return _TARGET_NOFILE, _TARGET_NOFILE
    clamped = min(_TARGET_NOFILE, hard)
    return clamped, clamped


# (id, start soft, target hard, needs CAP_SYS_RESOURCE)
_SCENARIOS = [
    # Low hard, no cap: the core fix path -- soft clamps up to the hard limit,
    # hard is never lowered, prlimit falls back to the raylet's own hard.
    ('low_hard_no_cap', 1024, 524288, False),
    # High hard (> 1048576), no cap: ulimit raises soft to the 1048576 target
    # while leaving the large hard limit intact -- proves `-Sn` (soft-only)
    # does not lower an already-high hard limit.
    ('high_hard_no_cap', 1024, 2000000, False),
    # Hard exactly at the target: soft reaches 1048576 exactly.
    ('exact_attainable', 1024, _TARGET_NOFILE, False),
    # With CAP_SYS_RESOURCE and a tiny hard limit: prlimit's first attempt
    # raises BOTH limits to 1048576 (documents the capability path).
    ('with_cap', 1024, 1024, True),
]


@pytest.mark.skipif(_DOCKER is None, reason='docker CLI not found on PATH')
@pytest.mark.parametrize('name,soft,hard,cap',
                         _SCENARIOS,
                         ids=[s[0] for s in _SCENARIOS])
def test_nofile_clamp_scenarios(name: str, soft: int, hard: int,
                                cap: bool) -> None:
    """Real ulimit + prlimit clamp behavior under a range of host limits."""
    if not _daemon_reachable():
        pytest.skip('docker daemon not reachable')
    if cap and not _cap_sys_resource_available():
        pytest.skip('CAP_SYS_RESOURCE not available to containers here')
    if hard > _max_hard_nofile():
        pytest.skip(f'daemon max hard nofile {_max_hard_nofile()} < {hard}')

    result = _run_scenario(soft, hard, cap)

    u_soft, u_hard = _soft_hard(result['ULIMIT_AFTER'])
    assert (u_soft, u_hard) == _expected_ulimit(hard), (
        f'ulimit: got soft={u_soft} hard={u_hard}, '
        f'expected {_expected_ulimit(hard)}')

    assert result['PRLIMIT_EXIT'] == '0', (
        f'prlimit exited {result["PRLIMIT_EXIT"]}')
    d_soft, d_hard = _soft_hard(result['DUMMY_AFTER'])
    assert (d_soft, d_hard) == _expected_prlimit(
        hard, cap), (f'prlimit: got soft={d_soft} hard={d_hard}, '
                     f'expected {_expected_prlimit(hard, cap)}')


@pytest.mark.skipif(_DOCKER is None, reason='docker CLI not found on PATH')
def test_nofile_clamp_at_daemon_ceiling() -> None:
    """Exercise the clamp live even where the daemon caps nofile low.

    Nested/sandboxed daemons cannot grant a high hard limit, so the fixed
    scenarios above skip. This runs the real commands at whatever hard limit
    the daemon *can* provide, guaranteeing live coverage of the fallback clamp
    (soft raised to the process's own hard limit, hard never lowered).
    """
    if not _daemon_reachable():
        pytest.skip('docker daemon not reachable')
    hard = _max_hard_nofile()
    if hard == 0:
        pytest.skip('daemon rejected every probed --ulimit nofile value')

    soft = min(1024, hard)
    result = _run_scenario(soft, hard, cap=False)

    u_soft, u_hard = _soft_hard(result['ULIMIT_AFTER'])
    assert (u_soft, u_hard) == _expected_ulimit(hard)
    assert result['PRLIMIT_EXIT'] == '0'
    d_soft, d_hard = _soft_hard(result['DUMMY_AFTER'])
    assert (d_soft, d_hard) == _expected_prlimit(hard, cap=False)


def _render_kubernetes_ray_template() -> str:
    """Render kubernetes-ray.yml.j2 via the snapshot test's own helpers.

    Loaded by file path so this test does not depend on the snapshot module's
    package layout.
    """
    import importlib.util
    import pathlib
    snapshot = (pathlib.Path(__file__).parents[1] / 'test_sky' / 'clouds' /
                'test_kubernetes_ray_template_snapshot.py')
    spec = importlib.util.spec_from_file_location('_k8s_ray_snapshot', snapshot)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._render(module._build_variables('base_cpu'))


def _yaml_scalars_containing(obj, needle: str, path: str = '') -> List[str]:
    """Paths of every YAML scalar that contains ``needle``."""
    hits: List[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            hits += _yaml_scalars_containing(value, needle, f'{path}/{key}')
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits += _yaml_scalars_containing(value, needle, f'{path}[{i}]')
    elif isinstance(obj, str) and needle in obj:
        hits.append(path)
    return hits


def test_ulimit_constant_inlined_at_both_template_sites() -> None:
    """RAISE_NOFILE_SOFT_LIMIT_CMD appears verbatim at both template sites.

    No docker needed. Binds the two inline template literals to the source
    constant: the pod entrypoint (a container command/args) and the Ray
    ``setup_commands``. Editing either the template or the constant alone fails
    this test.
    """
    import yaml
    rendered = _render_kubernetes_ray_template()
    doc = yaml.safe_load(rendered)
    hits = _yaml_scalars_containing(doc, RAISE_NOFILE_SOFT_LIMIT_CMD)

    entrypoint = [
        h for h in hits if 'command' in h.lower() or 'args' in h.lower()
    ]
    setup = [h for h in hits if 'setup_commands' in h.lower()]
    assert entrypoint, (
        'RAISE_NOFILE_SOFT_LIMIT_CMD not found in any container command/args '
        f'of the rendered manifest; matches: {hits}')
    assert setup, (
        'RAISE_NOFILE_SOFT_LIMIT_CMD not found in setup_commands of the '
        f'rendered manifest; matches: {hits}')
