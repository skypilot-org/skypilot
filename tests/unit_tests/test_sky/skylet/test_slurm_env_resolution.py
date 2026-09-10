"""Regression tests for SKY_SLURM_UNSET_PYTHONPATH's `env` resolution.

The Slurm backend expands SKY_SLURM_UNSET_PYTHONPATH inside a command that runs
under `bash -c` (see task_codegen.py's build_task_runner_cmd and
slurm/instance.py's _srun_on_node). `srun --export=ALL` re-imports the submit
host's exported bash functions into that shell, so the `env` resolver must be
robust against two of them:

  * an exported Debian/Ubuntu `which` function -- a minimal image's
    `/usr/bin/which` prints "Usage: ..." to stdout, which would poison a
    `$(which env ...)` substitution and make the run command start with
    `Usage:` (exit 127);
  * an exported `env` function -- it would be invoked in place of the binary.

These tests execute the resolver under bash with each function exported and
assert it still runs the real `env`. No Slurm, Pyxis, or Docker is needed.
"""

import shutil
import subprocess

import pytest

from sky.skylet import constants

# A `which` function like the one Debian/Ubuntu export, but pointed at a stub
# that prints a usage banner to stdout -- the exact poison mechanism. The fix
# must never call `which`, so this banner must not reach the resolved command.
_WHICH_FUNCTION_PREAMBLE = (
    'which() { echo "Usage: /usr/bin/which [-as] args"; }; export -f which')

# An exported `env` function that would hijack a bare-name resolution.
_ENV_FUNCTION_PREAMBLE = (
    'env() { echo INTERCEPTED "$@"; }; export -f env')

_SENTINEL = 'RESOLVED_OK'


def _run_resolver(preamble: str) -> subprocess.CompletedProcess:
    """Run SKY_SLURM_UNSET_PYTHONPATH under bash after a setup preamble.

    Args:
        preamble: shell statements exported before the resolver runs (e.g. an
            adversarial function definition).

    Returns:
        The completed process; stdout should contain the sentinel when the
        real `env` binary was resolved and executed.
    """
    resolver = constants.SKY_SLURM_UNSET_PYTHONPATH
    script = f'{preamble}\n{resolver} echo {_SENTINEL}'
    return subprocess.run(['bash', '-c', script],
                          capture_output=True,
                          text=True,
                          check=False)


@pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')
@pytest.mark.parametrize('preamble', [
    pytest.param('', id='baseline'),
    pytest.param(_WHICH_FUNCTION_PREAMBLE, id='exported-which-function'),
    pytest.param(_ENV_FUNCTION_PREAMBLE, id='exported-env-function'),
])
def test_env_resolver_runs_real_binary(preamble: str) -> None:
    """The resolver runs the real `env` despite adversarial exported functions.

    Args:
        preamble: an exported-function definition (or empty for the baseline).
    """
    result = _run_resolver(preamble)
    assert result.returncode == 0, result.stderr
    assert _SENTINEL in result.stdout
    # The `which`-poison banner must never reach the run command...
    assert 'Usage:' not in result.stdout
    # ...and an exported `env` function must never be invoked.
    assert 'INTERCEPTED' not in result.stdout
