"""Tests for the consolidation-mode env-leak fix.

When a request handler is in-flight, its os.environ has been mutated by
`override_request_env_and_config` to include the client's env_vars (most
problematically, `SKYPILOT_API_SERVER_ENDPOINT`). The bug is that the
spawned consolidation-mode controller subprocess chain
(`worker -> bash -> scheduler -> nohup -> sky.jobs.controller`) inherits
that polluted env for the controller's entire lifetime.

The fix has two layers:
  1. recovery_strategy.py clears `SKY_API_SERVER_URL_ENV_VAR` (and
     invalidates the lru_cache on get_server_url/is_api_server_local)
     before calling api_start, so already-leaked controllers self-heal.
  2. `LocalProcessCommandRunner.run` (the only CommandRunner that inherits
     the calling process's env into the child) passes a captured clean
     server env to subprocess.Popen instead of letting it default to
     os.environ, breaking the inheritance chain at its root for any
     consolidation-mode spawn.

These tests pin both layers.
"""
# pylint: disable=invalid-name,protected-access
import os
import subprocess
import tempfile

from sky.jobs import recovery_strategy
from sky.server.requests import executor as request_executor
from sky.skylet import constants
from sky.utils import command_runner


class TestCleanServerEnvCapture:
    """Layer 2 capture/getter behavior."""

    def setup_method(self):
        # Force a fresh capture for each test.
        request_executor._clean_server_env = None  # pylint: disable=protected-access

    def test_capture_then_get_returns_snapshot(self):
        os.environ['SKY_TEST_CAPTURE_KEY'] = 'captured_value'
        try:
            request_executor.capture_clean_server_env()
            # Mutate env AFTER capture — simulates a request mutating
            # os.environ via override_request_env_and_config.
            os.environ['SKY_TEST_CAPTURE_KEY'] = 'mutated_after_capture'
            os.environ['SKY_TEST_NEW_KEY'] = 'added_after_capture'

            snapshot = request_executor.get_clean_server_env()
            assert snapshot['SKY_TEST_CAPTURE_KEY'] == 'captured_value'
            assert 'SKY_TEST_NEW_KEY' not in snapshot
        finally:
            os.environ.pop('SKY_TEST_CAPTURE_KEY', None)
            os.environ.pop('SKY_TEST_NEW_KEY', None)

    def test_capture_is_idempotent(self):
        os.environ['SKY_TEST_IDEMPOTENT'] = 'first'
        try:
            request_executor.capture_clean_server_env()
            os.environ['SKY_TEST_IDEMPOTENT'] = 'second'
            request_executor.capture_clean_server_env()  # second call no-op
            snapshot = request_executor.get_clean_server_env()
            assert snapshot['SKY_TEST_IDEMPOTENT'] == 'first'
        finally:
            os.environ.pop('SKY_TEST_IDEMPOTENT', None)

    def test_get_returns_copy_not_reference(self):
        request_executor.capture_clean_server_env()
        snap1 = request_executor.get_clean_server_env()
        snap1['MUTATED_AFTER_GET'] = 'should_not_leak'
        snap2 = request_executor.get_clean_server_env()
        assert 'MUTATED_AFTER_GET' not in snap2

    def test_get_before_capture_falls_back_to_current_env(self):
        # Don't call capture_clean_server_env().
        os.environ['SKY_TEST_FALLBACK'] = 'fallback_val'
        try:
            snap = request_executor.get_clean_server_env()
            # Fallback returns current os.environ — correctness is "doesn't
            # crash and returns a useful dict", not "matches the
            # pre-pollution state" (which it can't, since nothing captured).
            assert snap.get('SKY_TEST_FALLBACK') == 'fallback_val'
        finally:
            os.environ.pop('SKY_TEST_FALLBACK', None)


class TestPopenEnvOverrideMechanism:
    """The fundamental mechanism: subprocess.Popen with env= breaks the
    inheritance chain at the root."""

    def test_default_popen_inherits_polluted_env(self):
        os.environ['SKY_TEST_LEAK_DEFAULT'] = 'leaked'
        try:
            result = subprocess.run(
                ['bash', '-c', 'echo "$SKY_TEST_LEAK_DEFAULT"'],
                capture_output=True,
                text=True,
                check=True)
            assert result.stdout.strip() == 'leaked'
        finally:
            os.environ.pop('SKY_TEST_LEAK_DEFAULT', None)

    def test_popen_with_explicit_env_does_not_inherit(self):
        os.environ['SKY_TEST_LEAK_EXPLICIT'] = 'leaked'
        try:
            clean = {
                k: v
                for k, v in os.environ.items()
                if k != 'SKY_TEST_LEAK_EXPLICIT'
            }
            # Ensure PATH is preserved so bash itself can run.
            assert 'PATH' in clean
            result = subprocess.run(
                ['bash', '-c', 'echo "${SKY_TEST_LEAK_EXPLICIT:-<unset>}"'],
                env=clean,
                capture_output=True,
                text=True,
                check=True)
            assert result.stdout.strip() == '<unset>'
        finally:
            os.environ.pop('SKY_TEST_LEAK_EXPLICIT', None)


class TestRecoveryStrategyBeltAndSuspenders:
    """Layer 1: ENV_VARS_TO_CLEAR includes the endpoint var."""

    def test_endpoint_env_var_is_cleared_before_api_start(self):
        assert (
            constants.SKY_API_SERVER_URL_ENV_VAR
            in recovery_strategy.ENV_VARS_TO_CLEAR), (
                'SKY_API_SERVER_URL_ENV_VAR must be in ENV_VARS_TO_CLEAR so '
                'already-leaked controllers can call sdk.api_start() and have '
                'get_server_url() resolve to the local default. This is the '
                'one-line escape hatch for controllers that were spawned with '
                'a polluted env before the plumbing fix landed.')


class TestLocalProcessCommandRunnerUsesCleanEnv:
    """LocalProcessCommandRunner.run is the only CommandRunner that inherits
    the calling process's env into the child process (subprocess.Popen). It
    must default to the pre-pollution server env snapshot, not the worker's
    current os.environ — otherwise per-request env mutations from
    override_request_env_and_config leak into long-lived consolidation-mode
    controllers spawned via run_on_head -> run_driver -> run.
    """

    def test_run_defaults_to_clean_server_env(self):
        # Pre-populate the clean snapshot with a known value, and a value
        # that's deliberately ABSENT — so we can tell the snapshot was used
        # rather than current os.environ.
        request_executor._clean_server_env = {  # pylint: disable=protected-access
            'PATH': os.environ.get('PATH', '/usr/bin'),
            'CLEAN_MARKER': 'clean_value',
        }
        os.environ['SKY_TEST_RUN_LEAK'] = 'should_not_appear'
        try:
            runner = command_runner.LocalProcessCommandRunner()
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = os.path.join(tmpdir, 'run.log')
                out_path = os.path.join(tmpdir, 'out.txt')
                rc = runner.run(
                    'echo "leak=${SKY_TEST_RUN_LEAK:-<unset>} '
                    f'clean=${{CLEAN_MARKER:-<unset>}}" > {out_path}',
                    log_path=log_path,
                    stream_logs=False,
                    process_stream=True,
                    require_outputs=False)
                assert rc == 0
                with open(out_path, encoding='utf-8') as f:
                    body = f.read().strip()
            assert body == 'leak=<unset> clean=clean_value', (
                f'Subprocess saw {body!r}: SKY_TEST_RUN_LEAK should be '
                'absent (came from caller env) and CLEAN_MARKER should be '
                'present (from clean snapshot).')
        finally:
            os.environ.pop('SKY_TEST_RUN_LEAK', None)
            request_executor._clean_server_env = None  # pylint: disable=protected-access

    def test_run_honors_explicit_env_override(self):
        # Caller can still pass env= to override; useful for tests / callers
        # that want a specific env.
        request_executor._clean_server_env = {  # pylint: disable=protected-access
            'PATH': os.environ.get('PATH', '/usr/bin'),
            'CLEAN_MARKER': 'clean_value',
        }
        try:
            runner = command_runner.LocalProcessCommandRunner()
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = os.path.join(tmpdir, 'run.log')
                out_path = os.path.join(tmpdir, 'out.txt')
                rc = runner.run(
                    f'echo "marker=${{CLEAN_MARKER:-<unset>}} '
                    f'override=${{OVERRIDE_MARKER:-<unset>}}" > {out_path}',
                    log_path=log_path,
                    stream_logs=False,
                    process_stream=True,
                    require_outputs=False,
                    env={
                        'PATH': os.environ.get('PATH', '/usr/bin'),
                        'OVERRIDE_MARKER': 'override_value',
                    })
                assert rc == 0
                with open(out_path, encoding='utf-8') as f:
                    body = f.read().strip()
            # Explicit env wins: CLEAN_MARKER (which is in the snapshot) is
            # NOT in the explicit env, so it should be unset.
            assert body == 'marker=<unset> override=override_value', body
        finally:
            request_executor._clean_server_env = None  # pylint: disable=protected-access
