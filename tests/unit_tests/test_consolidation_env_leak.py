"""Tests for the consolidation-mode env-leak fix (GitHub issue / SKY-5504).

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
  2. `_consolidated_launch` and the serve mirror pass an explicit clean
     env to `run_on_head`, breaking the inheritance chain at its root.

These tests pin both layers.
"""
# pylint: disable=invalid-name,protected-access
import os
import subprocess
import sys
import tempfile
from unittest import mock

import pytest

from sky.jobs import recovery_strategy
from sky.server import common as server_common
from sky.server.requests import executor as request_executor
from sky.skylet import constants


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
                capture_output=True, text=True, check=True)
            assert result.stdout.strip() == 'leaked'
        finally:
            os.environ.pop('SKY_TEST_LEAK_DEFAULT', None)

    def test_popen_with_explicit_env_does_not_inherit(self):
        os.environ['SKY_TEST_LEAK_EXPLICIT'] = 'leaked'
        try:
            clean = {k: v for k, v in os.environ.items()
                     if k != 'SKY_TEST_LEAK_EXPLICIT'}
            # Ensure PATH is preserved so bash itself can run.
            assert 'PATH' in clean
            result = subprocess.run(
                ['bash', '-c',
                 'echo "${SKY_TEST_LEAK_EXPLICIT:-<unset>}"'],
                env=clean, capture_output=True, text=True, check=True)
            assert result.stdout.strip() == '<unset>'
        finally:
            os.environ.pop('SKY_TEST_LEAK_EXPLICIT', None)


class TestRecoveryStrategyBeltAndSuspenders:
    """Layer 1: ENV_VARS_TO_CLEAR includes the endpoint var."""

    def test_endpoint_env_var_is_cleared_before_api_start(self):
        assert (constants.SKY_API_SERVER_URL_ENV_VAR
                in recovery_strategy.ENV_VARS_TO_CLEAR), (
            'SKY_API_SERVER_URL_ENV_VAR must be in ENV_VARS_TO_CLEAR so '
            'already-leaked controllers can call sdk.api_start() and have '
            'get_server_url() resolve to the local default. This is the '
            'one-line escape hatch for controllers that were spawned with '
            'a polluted env before the plumbing fix landed.')


class TestRunOnHeadForwardsEnvKwarg:
    """`backend.run_on_head` must thread env through to LocalProcessCommandRunner.

    This is the actual fix point: if env is dropped anywhere between
    run_on_head and the underlying subprocess.Popen, the leak comes back.
    """

    def test_local_runner_run_accepts_env(self):
        # Smoke-check: LocalProcessCommandRunner.run accepts env= without
        # raising, and runs a trivial command.
        from sky.utils import command_runner  # local import
        runner = command_runner.LocalProcessCommandRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'run.log')
            # Set a leak in our own env to make sure the spawned bash does
            # not inherit it when we pass env=clean.
            os.environ['SKY_TEST_RUN_LEAK'] = 'should_not_appear'
            try:
                clean = {k: v for k, v in os.environ.items()
                         if k != 'SKY_TEST_RUN_LEAK'}
                # Use stream_logs=True / process_stream=False so output
                # lands in the log file via tee.
                rc = runner.run(
                    'env | grep -E "^SKY_TEST_RUN_LEAK=" > '
                    f'{tmpdir}/leak.txt || true; echo done',
                    log_path=log_path,
                    stream_logs=False,
                    process_stream=True,
                    require_outputs=False,
                    env=clean)
                assert rc == 0
                leak_path = os.path.join(tmpdir, 'leak.txt')
                if os.path.exists(leak_path):
                    with open(leak_path, encoding='utf-8') as f:
                        body = f.read().strip()
                    assert body == '', (
                        f'Subprocess saw leaked env: {body!r}')
            finally:
                os.environ.pop('SKY_TEST_RUN_LEAK', None)


class TestConsolidatedLaunchPassesCleanEnv:
    """The plumbing test: _consolidated_launch should hand a clean env to
    backend.run_on_head, NOT the worker's current (polluted) os.environ.
    """

    def test_consolidated_launch_forwards_clean_env(self):
        # Capture a known-clean snapshot.
        request_executor._clean_server_env = {  # pylint: disable=protected-access
            'PATH': '/usr/bin', 'CLEAN_MARKER': '1'
        }
        # Now simulate per-request pollution in os.environ.
        os.environ['SKY_TEST_POLLUTION'] = 'polluted'
        try:
            # Patch run_on_head to record what env was passed.
            captured = {}
            fake_backend = mock.MagicMock()

            def record_env(*args, **kwargs):
                captured['env'] = kwargs.get('env')
                captured['kwargs_keys'] = list(kwargs.keys())
                return None
            fake_backend.run_on_head.side_effect = record_env
            fake_backend.sync_file_mounts.return_value = None
            from sky import backends as _backends
            fake_backend.__class__ = _backends.CloudVmRayBackend

            fake_handle = mock.MagicMock()

            with mock.patch.object(
                    __import__('sky.backends.backend_utils', fromlist=['x']),
                    'is_controller_accessible', return_value=fake_handle), \
                 mock.patch.object(
                    __import__('sky.backends.backend_utils', fromlist=['x']),
                    'get_backend_from_handle', return_value=fake_backend), \
                 mock.patch(
                    'sky.jobs.server.core.sky_logging.silent',
                    return_value=__import__('contextlib').nullcontext()):
                # Build a minimal controller_task stub.
                controller_task = mock.MagicMock()
                controller_task.run = 'echo run-script'
                controller_task.envs = {'CONTROLLER_ENV': 'present'}
                controller_task.file_mounts = {}
                controller_task.storage_mounts = []

                from sky.jobs.server import core as jobs_core
                jobs_core._consolidated_launch(
                    controller=mock.MagicMock(),
                    controller_task=controller_task,
                    job_ids=[1])

            assert captured.get('env') is not None, (
                'run_on_head was called without env=; the plumbing fix is '
                'missing or regressed.')
            assert captured['env'].get('CLEAN_MARKER') == '1'
            assert 'SKY_TEST_POLLUTION' not in captured['env'], (
                'env passed to run_on_head includes the polluted var; the '
                'snapshot must be the pre-pollution capture, NOT the '
                'current os.environ.')
        finally:
            os.environ.pop('SKY_TEST_POLLUTION', None)
            request_executor._clean_server_env = None  # pylint: disable=protected-access
