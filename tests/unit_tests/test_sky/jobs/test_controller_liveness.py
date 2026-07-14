"""Unit tests for sky.jobs.controller_liveness."""
import os
import subprocess
import sys
from unittest import mock

import psutil
import pytest

from sky.jobs import controller_liveness
from sky.jobs import state as managed_job_state

ControllerLiveness = controller_liveness.ControllerLiveness


@pytest.fixture
def _restore_liveness_provider():
    """Restore the module-level registered provider after the test.

    register()/get_provider() mutate controller_liveness._provider directly
    (the registry pattern documented in the module docstring), so a fake
    provider registered by one test would otherwise leak into whichever
    test runs next.
    """
    original = controller_liveness._provider
    try:
        yield
    finally:
        controller_liveness._provider = original


class TestLocalPidVerdict:
    """Matrix for controller_liveness.local_pid_verdict."""

    def test_own_process_is_alive(self):
        pid = os.getpid()
        started_at = psutil.Process(pid).create_time()
        record = managed_job_state.ControllerPidRecord(pid=pid,
                                                       started_at=started_at)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.ALIVE)

    def test_dead_pid_is_dead(self):
        """A pid that has already exited and been reaped is DEAD."""
        proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        proc.wait(timeout=5)
        record = managed_job_state.ControllerPidRecord(pid=proc.pid,
                                                       started_at=None)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.DEAD)

    def test_create_time_mismatch_is_dead(self):
        """A live process whose create_time doesn't match is DEAD.

        This is the "pid got reused by an unrelated process" case.
        """
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(10)'])
        try:
            real_started_at = psutil.Process(proc.pid).create_time()
            record = managed_job_state.ControllerPidRecord(
                pid=proc.pid, started_at=real_started_at - 1000)
            assert (controller_liveness.local_pid_verdict(record) ==
                    ControllerLiveness.DEAD)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_access_denied_is_dead(self, monkeypatch):
        """psutil.AccessDenied -> DEAD: controller processes are spawned by
        the same user running this check, so a pid we cannot inspect belongs
        to some other user's process, not the controller."""

        class _FakeProcess:

            def __init__(self, pid):
                del pid  # unused

            def create_time(self):
                raise psutil.AccessDenied()

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=1234,
                                                       started_at=1700000000.0)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.DEAD)

    def test_os_error_is_unknown(self, monkeypatch):
        """A generic OSError (e.g. a transient failure) can't tell us the
        process is gone -> UNKNOWN, unlike AccessDenied."""

        class _FakeProcess:

            def __init__(self, pid):
                del pid  # unused

            def create_time(self):
                raise OSError('transient failure')

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=1234,
                                                       started_at=1700000000.0)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.UNKNOWN)

    def test_no_such_process_is_dead(self, monkeypatch):

        def _raise_no_such_process(pid):
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr(controller_liveness.psutil, 'Process',
                            _raise_no_such_process)
        record = managed_job_state.ControllerPidRecord(pid=1234,
                                                       started_at=1700000000.0)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.DEAD)

    def test_zombie_process_is_dead(self, monkeypatch):

        class _FakeProcess:

            def __init__(self, pid):
                self._pid = pid

            def create_time(self):
                raise psutil.ZombieProcess(self._pid)

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=1234,
                                                       started_at=1700000000.0)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.DEAD)

    def test_unexpected_exception_is_unknown(self, monkeypatch):

        class _FakeProcess:

            def __init__(self, pid):
                del pid  # unused

            def create_time(self):
                raise RuntimeError('boom')

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=1234,
                                                       started_at=1700000000.0)
        assert (controller_liveness.local_pid_verdict(record) ==
                ControllerLiveness.UNKNOWN)

    def test_legacy_matching_cmdline_and_job_id_is_alive(self, monkeypatch):
        """started_at=None: fall back to the pre-#7051 cmdline check."""
        expected_pid = 2468
        monkeypatch.setattr(controller_liveness.psutil, 'pid_exists',
                            lambda pid: pid == expected_pid)

        class _FakeProcess:

            def __init__(self, pid):
                assert pid == expected_pid

            def cmdline(self):
                return [
                    'python', '-m', 'sky.jobs.controller', 'dag.yaml',
                    '--job-id', '42'
                ]

            def is_running(self):
                return True

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=expected_pid,
                                                       started_at=None)
        assert (controller_liveness.local_pid_verdict(
            record, legacy_job_id=42) == ControllerLiveness.ALIVE)

    def test_legacy_wrong_job_id_is_dead(self, monkeypatch):
        expected_pid = 2468
        monkeypatch.setattr(controller_liveness.psutil, 'pid_exists',
                            lambda pid: pid == expected_pid)

        class _FakeProcess:

            def __init__(self, pid):
                assert pid == expected_pid

            def cmdline(self):
                return [
                    'python', '-m', 'sky.jobs.controller', 'dag.yaml',
                    '--job-id', '42'
                ]

            def is_running(self):
                return True

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=expected_pid,
                                                       started_at=None)
        # legacy_job_id=99 doesn't match the --job-id 42 in the cmdline.
        assert (controller_liveness.local_pid_verdict(
            record, legacy_job_id=99) == ControllerLiveness.DEAD)

    def test_legacy_missing_keyword_is_dead(self, monkeypatch):

        class _FakeProcess:

            def __init__(self, pid):
                del pid  # unused

            def cmdline(self):
                return ['python', '-m', 'some.other.module']

        monkeypatch.setattr(controller_liveness.psutil, 'Process', _FakeProcess)
        record = managed_job_state.ControllerPidRecord(pid=2468,
                                                       started_at=None)
        assert (controller_liveness.local_pid_verdict(
            record, legacy_job_id=42) == ControllerLiveness.DEAD)


class TestJobOwnerRecordFromJobRow:
    """JobOwnerRecord.from_job_row: dict-based construction with legacy
    negative-pid normalization."""

    def test_positive_pid_passes_through(self):
        row = {
            'controller_pid': 100,
            'controller_pid_started_at': 1.0,
            'controller_server_id': 'server-a',
        }
        owner = controller_liveness.JobOwnerRecord.from_job_row(
            row, legacy_job_id=42)
        assert owner.pid == 100
        assert owner.pid_started_at == 1.0
        assert owner.server_id == 'server-a'
        assert owner.legacy_job_id == 42

    def test_negative_pid_is_normalized_to_absolute(self):
        """Between #7051 and #7847 a negative pid marked a multi-job
        controller; from_job_row must normalize it so downstream liveness
        checks (e.g. psutil) see a real pid."""
        row = {
            'controller_pid': -100,
            'controller_pid_started_at': 1.0,
            'controller_server_id': None,
        }
        owner = controller_liveness.JobOwnerRecord.from_job_row(row)
        assert owner.pid == 100

    def test_none_pid_stays_none(self):
        row = {
            'controller_pid': None,
            'controller_pid_started_at': None,
            'controller_server_id': None,
        }
        owner = controller_liveness.JobOwnerRecord.from_job_row(row)
        assert owner.pid is None

    def test_missing_keys_default_to_none(self):
        """Rows that don't carry these columns at all (e.g. a narrower
        field selection) shouldn't raise -- .get() everything."""
        owner = controller_liveness.JobOwnerRecord.from_job_row({})
        assert owner.pid is None
        assert owner.pid_started_at is None
        assert owner.server_id is None
        assert owner.legacy_job_id is None


class TestLocalPidLivenessProvider:

    def test_pid_none_is_dead(self):
        """No controller was ever stamped for this job."""
        provider = controller_liveness.LocalPidLivenessProvider()
        owner = controller_liveness.JobOwnerRecord(pid=None,
                                                   pid_started_at=None,
                                                   server_id=None)
        assert provider.check(owner) == ControllerLiveness.DEAD

    def test_ignores_server_id(self):
        """The local provider only knows about pids on this machine."""
        provider = controller_liveness.LocalPidLivenessProvider()
        pid = os.getpid()
        started_at = psutil.Process(pid).create_time()
        owner = controller_liveness.JobOwnerRecord(
            pid=pid,
            pid_started_at=started_at,
            server_id='some-other-server-entirely')
        assert provider.check(owner) == ControllerLiveness.ALIVE

    def test_does_not_handle_remote_owners(self):
        """A local-pid-only provider's verdicts about a job claimed by a
        different server instance are not authoritative -- the janitor's
        remote-owner recovery branch (sky.jobs.utils.
        update_managed_jobs_statuses) must not run against it. This is also
        the ControllerLivenessProvider ABC's default."""
        assert controller_liveness.LocalPidLivenessProvider.handles_remote_owners is False


class TestHandlesRemoteOwnersDefault:
    """ControllerLivenessProvider.handles_remote_owners defaults to False."""

    def test_abc_default_is_false(self):
        assert controller_liveness.ControllerLivenessProvider.handles_remote_owners is False


class TestRegistry:
    """register()/get_provider() default + override semantics."""

    def test_default_provider_is_local_pid(self, _restore_liveness_provider):
        controller_liveness._provider = None
        provider = controller_liveness.get_provider()
        assert isinstance(provider,
                          controller_liveness.LocalPidLivenessProvider)

    def test_get_provider_is_stable_across_calls(self,
                                                 _restore_liveness_provider):
        controller_liveness._provider = None
        first = controller_liveness.get_provider()
        second = controller_liveness.get_provider()
        assert first is second

    def test_register_overrides_default(self, _restore_liveness_provider):
        fake = mock.create_autospec(
            controller_liveness.ControllerLivenessProvider, instance=True)
        controller_liveness.register(fake)
        assert controller_liveness.get_provider() is fake

    def test_last_registration_wins(self, _restore_liveness_provider):
        first = mock.create_autospec(
            controller_liveness.ControllerLivenessProvider, instance=True)
        second = mock.create_autospec(
            controller_liveness.ControllerLivenessProvider, instance=True)
        controller_liveness.register(first)
        controller_liveness.register(second)
        assert controller_liveness.get_provider() is second


class TestCheckJobOwner:
    """check_job_owner: dispatch to the registered provider, and collapse
    any provider exception to UNKNOWN."""

    def test_uses_registered_provider(self, _restore_liveness_provider):
        fake = mock.create_autospec(
            controller_liveness.ControllerLivenessProvider, instance=True)
        fake.check.return_value = ControllerLiveness.DEAD
        controller_liveness.register(fake)
        owner = controller_liveness.JobOwnerRecord(pid=1,
                                                   pid_started_at=1.0,
                                                   server_id=None)
        assert (controller_liveness.check_job_owner(owner) ==
                ControllerLiveness.DEAD)
        fake.check.assert_called_once_with(owner)

    def test_provider_exception_collapses_to_unknown(
            self, _restore_liveness_provider):
        fake = mock.create_autospec(
            controller_liveness.ControllerLivenessProvider, instance=True)
        fake.check.side_effect = RuntimeError('unreachable registry')
        controller_liveness.register(fake)
        owner = controller_liveness.JobOwnerRecord(pid=1,
                                                   pid_started_at=1.0,
                                                   server_id='remote')
        assert (controller_liveness.check_job_owner(owner) ==
                ControllerLiveness.UNKNOWN)
