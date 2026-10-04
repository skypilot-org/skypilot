"""Tests for smoke_tests_utils.run_one_test() status tracking.

Keep 'smoke_tests' out of this file's path: tests/conftest.py treats a session
as a smoke-test session when any collected item's path contains it, which
wraps the whole session in a config override.
"""
import os

import pytest
from smoke_tests import smoke_tests_utils


@pytest.fixture
def quiet_failure_reporting(monkeypatch):
    """Stop the failure path from shelling out to the real API server.

    On failure `run_one_test` runs `fetch_failed_job_logs.sh` (which calls
    `sky jobs queue`) and tails the server log. Neither belongs in a unit
    test, and both are gated on `os.path.exists`, so denying just those two
    paths takes the documented "not found" branch instead.
    """
    real_exists = os.path.exists

    def fake_exists(path):
        if str(path).endswith(('fetch_failed_job_logs.sh', 'server.log')):
            return False
        return real_exists(path)

    monkeypatch.setattr(smoke_tests_utils.os.path, 'exists', fake_exists)
    # Log to stderr rather than opening a temp log file per test.
    monkeypatch.setenv('LOG_TO_STDOUT', '1')


@pytest.fixture
def quiet_reporting(monkeypatch):
    """The success path's counterpart: only the log-to-stdout switch."""
    monkeypatch.setenv('LOG_TO_STDOUT', '1')


def test_callable_first_failure_reports_the_test_failure(
        quiet_failure_reporting):
    """A callable failing as the FIRST command must fail as a test failure.

    Regression: the failure branch used to record the status by mutating
    `proc.returncode`, but `proc` is only bound by the subprocess branch. A
    test whose first command is a callable died with `UnboundLocalError`
    instead, which replaced the callable's real exception in the pytest
    summary and hid why the test failed.
    """

    def boom():
        raise RuntimeError('the real failure')

    test = smoke_tests_utils.Test('callable-first-failure', [boom])
    with pytest.raises(Exception) as exc_info:
        smoke_tests_utils.run_one_test(test, check_sky_status=False)
    # Not an UnboundLocalError, and not the raw RuntimeError: the runner
    # reports its own failure after logging the callable's traceback.
    assert not isinstance(exc_info.value, UnboundLocalError)
    assert str(exc_info.value) == 'test failed'


def test_all_callables_succeeding_passes(quiet_reporting):
    """A test made only of callables must pass.

    Same unbound `proc`, reached from the other side: with no subprocess
    ever started, the reporting block had nothing to read the status from
    even when every callable succeeded.
    """
    calls = []

    test = smoke_tests_utils.Test(
        'all-callables', [lambda: calls.append('a'), lambda: calls.append('b')])
    smoke_tests_utils.run_one_test(test, check_sky_status=False)
    assert calls == ['a', 'b']


def test_callable_failure_stops_later_commands(quiet_failure_reporting,
                                               tmp_path):
    """A failing callable must not run the commands after it."""
    sentinel = tmp_path / 'ran'

    def boom():
        raise RuntimeError('stop here')

    test = smoke_tests_utils.Test('callable-short-circuits',
                                  [boom, f'touch {sentinel}'])
    with pytest.raises(Exception, match='test failed'):
        smoke_tests_utils.run_one_test(test, check_sky_status=False)
    assert not sentinel.exists()


def test_failing_shell_command_still_fails(quiet_failure_reporting):
    """The ordinary subprocess path is unchanged: a bad exit code fails."""
    test = smoke_tests_utils.Test('shell-failure', ['exit 3'])
    with pytest.raises(Exception, match='test failed'):
        smoke_tests_utils.run_one_test(test, check_sky_status=False)


def test_success_runs_teardown(quiet_reporting, tmp_path):
    """Teardown is gated on the tracked status, so it still runs on success."""
    sentinel = tmp_path / 'torn-down'
    test = smoke_tests_utils.Test('teardown-on-success', [lambda: None],
                                  teardown=f'touch {sentinel}')
    smoke_tests_utils.run_one_test(test, check_sky_status=False)
    assert sentinel.exists()
