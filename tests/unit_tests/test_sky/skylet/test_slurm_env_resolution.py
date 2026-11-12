"""Regression tests for SKY_SLURM_UNSET_PYTHONPATH's `env` invocation.

The Slurm backend expands SKY_SLURM_UNSET_PYTHONPATH inside a command that runs
under `bash -c` (see task_codegen.py's build_task_runner_cmd and
slurm/instance.py's _srun_on_node). `srun --export=ALL` re-imports the submit
host's exported bash functions into that shell, so running `env` must be
robust against:

  * an exported Debian/Ubuntu `which` function -- a minimal image's
    `/usr/bin/which` prints "Usage: ..." to stdout, which would poison a
    `$(which env ...)` substitution and make the run command start with
    `Usage:` (exit 127);
  * an exported `env` function -- it would be invoked in place of the binary;
  * a non-executable `env` shadowing the real one earlier on PATH (e.g.
    `$HOME/.local/bin/env` left by a uv installation) -- the shell's PATH
    search must skip it.

The constant uses `command env`, the POSIX invocation form that bypasses shell
functions and runs the utility from PATH. These tests execute it under bash
with each adversarial setup and assert the real `env` still runs. No Slurm,
Pyxis, or Docker is needed.
"""

import shutil
import subprocess

import pytest

from sky.skylet import constants

# A `which` function like the one Debian/Ubuntu export, but pointed at a stub
# that prints a usage banner to stdout -- the exact poison mechanism. The
# constant must never call `which`, so this banner must not reach the
# resolved command.
_WHICH_FUNCTION_PREAMBLE = (
    'which() { echo "Usage: /usr/bin/which [-as] args"; }; export -f which')

# An exported `env` function that would hijack a bare-name invocation.
_ENV_FUNCTION_PREAMBLE = ('env() { echo INTERCEPTED "$@"; }; export -f env')

# A non-executable `env` shadow first on PATH, like the one a uv installation
# leaves in ~/.local/bin. bash's PATH search must skip it and run the real
# binary.
_NONEXEC_SHADOW_PREAMBLE = (
    'shadow_dir=$(mktemp -d) && '
    'printf \'#!/bin/sh\\necho WRONG\\n\' > "$shadow_dir/env" && '
    'chmod 0644 "$shadow_dir/env" && '
    'export PATH="$shadow_dir:$PATH"')

_SENTINEL = 'RESOLVED_OK'


def _run_env(preamble: str) -> subprocess.CompletedProcess:
    """Run SKY_SLURM_UNSET_PYTHONPATH under bash after a setup preamble.

    Args:
        preamble: shell statements run before the constant (e.g. an
            adversarial function definition).

    Returns:
        The completed process; stdout should contain the sentinel when the
        real `env` binary was invoked.
    """
    script = (f'{preamble}\n'
              f'{constants.SKY_SLURM_UNSET_PYTHONPATH} echo {_SENTINEL}')
    return subprocess.run(['bash', '-c', script],
                          capture_output=True,
                          text=True,
                          check=False)


@pytest.mark.skipif(shutil.which('bash') is None, reason='bash not available')
@pytest.mark.parametrize('preamble', [
    pytest.param('', id='baseline'),
    pytest.param(_WHICH_FUNCTION_PREAMBLE, id='exported-which-function'),
    pytest.param(_ENV_FUNCTION_PREAMBLE, id='exported-env-function'),
    pytest.param(_NONEXEC_SHADOW_PREAMBLE, id='non-executable-path-shadow'),
])
def test_env_invocation_runs_real_binary(preamble: str) -> None:
    """The constant runs the real `env` despite adversarial exported state.

    Args:
        preamble: an adversarial setup (or empty for the baseline).
    """
    result = _run_env(preamble)
    assert result.returncode == 0, result.stderr
    assert _SENTINEL in result.stdout
    # The `which`-poison banner must never reach the run command...
    assert 'Usage:' not in result.stdout
    # ...an exported `env` function must never be invoked...
    assert 'INTERCEPTED' not in result.stdout
    # ...and a non-executable PATH shadow must never be executed.
    assert 'WRONG' not in result.stdout
