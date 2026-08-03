"""Tests for smoke_tests_utils.chain_teardown().

Keep 'smoke_tests' out of this file's path: tests/conftest.py treats a session
as a smoke-test session when any collected item's path contains it, which
wraps the whole session in a config override.
"""
import subprocess
from typing import List, Tuple

from smoke_tests import smoke_tests_utils


def _run(cmd: str) -> Tuple[int, List[str]]:
    proc = subprocess.run(cmd,
                          shell=True,
                          executable='/bin/bash',
                          capture_output=True,
                          text=True,
                          check=False)
    return proc.returncode, proc.stdout.split()


def test_chain_teardown_all_steps_succeed():
    cmd = smoke_tests_utils.chain_teardown('echo a', 'echo b')
    assert _run(cmd) == (0, ['a', 'b'])


def test_chain_teardown_failing_step_does_not_skip_the_rest():
    # The whole point of the helper: a failing `sky down` must not skip the
    # cloud-cmd helper cluster teardown, and must still fail the teardown.
    cmd = smoke_tests_utils.chain_teardown('false', 'echo helper-down')
    assert _run(cmd) == (1, ['helper-down'])


def test_chain_teardown_shares_shell_state():
    # Steps run in one shell, so a variable set by an earlier step is still
    # visible later; a trailing `;` must not break the grouping.
    cmd = smoke_tests_utils.chain_teardown('vols=1;', 'echo $vols')
    assert _run(cmd) == (0, ['1'])


def test_chain_teardown_nests():
    inner = smoke_tests_utils.chain_teardown('false', 'echo inner')
    cmd = smoke_tests_utils.chain_teardown(inner, 'echo outer')
    assert _run(cmd) == (1, ['inner', 'outer'])


def test_chain_teardown_keeps_tolerated_failures_tolerated():
    cmd = smoke_tests_utils.chain_teardown('false || true', 'echo b')
    assert _run(cmd) == (0, ['b'])
