"""Unit tests for sky.jobs.scheduler.kill_local_job_controllers.

Used during shutdown (lock-loss suicide and uvicorn graceful shutdown) to
prevent split-brain: this replica's controllers must not outlive the
moment another replica's refresh daemon could acquire the consolidation
lock. The helper must be best-effort — it runs on shutdown paths where
raising would either prevent SIGTERM or stall drain.
"""
import signal
from unittest import mock

from sky.jobs import controller_liveness
from sky.jobs import scheduler
from sky.jobs import state as managed_job_state


def _record(pid: int, started_at: float = 0.0):
    return managed_job_state.ControllerPidRecord(pid=pid, started_at=started_at)


class TestKillLocalConsolidationControllers:

    def test_no_pid_file_returns_zero(self):
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=[]):
            assert scheduler.kill_local_job_controllers() == 0

    def test_records_none_returns_zero(self):
        """Helper must tolerate the PID-file read failing (returns None)."""
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=None):
            assert scheduler.kill_local_job_controllers() == 0

    def test_signals_live_records(self):
        recs = [_record(101), _record(202), _record(303)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 3
        kill_mock.assert_has_calls([
            mock.call(101, signal.SIGTERM),
            mock.call(202, signal.SIGTERM),
            mock.call(303, signal.SIGTERM)
        ],
                                   any_order=True)

    def test_skips_dead_records(self):
        """Stale entries (process exited or wrong started_at) are skipped —
        otherwise we'd SIGTERM unrelated PIDs that the OS reused."""
        recs = [_record(101), _record(202)]
        alive_lookup = {101: True, 202: False}
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(
                    scheduler.managed_job_utils,
                    'controller_process_alive',
                    side_effect=lambda r: alive_lookup[r.pid]), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1
        kill_mock.assert_called_once_with(101, signal.SIGTERM)

    def test_tolerates_process_lookup_error(self):
        """Race between alive-check and kill: the PID died in between.
        Not counted as signaled, but doesn't abort the loop."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[ProcessLookupError(), None]) as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1  # Only the second succeeded.
        assert kill_mock.call_count == 2

    def test_continues_on_oserror(self):
        """Per-PID OSError (e.g. EPERM) must not stop the rest."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[OSError('EPERM'), None]):
            n = scheduler.kill_local_job_controllers()
        assert n == 1

    def test_custom_signal(self):
        recs = [_record(101)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            scheduler.kill_local_job_controllers(sig=signal.SIGKILL)
        kill_mock.assert_called_once_with(101, signal.SIGKILL)


class TestSubmitJobsLivenessGating:
    """submit_jobs: skip (re-)submission unless the controller verdict is DEAD.

    ALIVE and UNKNOWN are both treated as "don't touch it": we can't prove
    the previous controller is gone, so submitting again risks running the
    job twice.
    """

    def _write_files(self, tmp_path):
        dag_path = tmp_path / 'dag.yaml'
        user_yaml_path = tmp_path / 'user.yaml'
        env_path = tmp_path / 'env'
        dag_path.write_text('dag: contents')
        user_yaml_path.write_text('user: yaml')
        env_path.write_text('ENV=1')
        return str(dag_path), str(user_yaml_path), str(env_path)

    def _run(self, monkeypatch, tmp_path, owner, check_mock):
        dag_path, user_yaml_path, env_path = self._write_files(tmp_path)
        # Don't let an ambient SKYPILOT_CONFIG env var pull in a real file.
        monkeypatch.delenv(scheduler.skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                           raising=False)
        monkeypatch.setattr(scheduler.state, 'get_job_owner_record',
                            lambda job_id: owner)
        monkeypatch.setattr(scheduler.controller_liveness, 'check_job_owner',
                            check_mock)
        with mock.patch.object(scheduler.state,
                               'scheduler_set_waiting') as set_waiting_mock, \
                mock.patch.object(scheduler, 'maybe_start_controllers'):
            scheduler.submit_jobs([1],
                                  dag_path,
                                  user_yaml_path,
                                  env_path,
                                  priority=100)
        return set_waiting_mock

    def test_dead_owner_allows_submission(self, tmp_path, monkeypatch):
        owner = controller_liveness.JobOwnerRecord(pid=100,
                                                   pid_started_at=1.0,
                                                   server_id=None)
        check_mock = mock.MagicMock(
            return_value=controller_liveness.ControllerLiveness.DEAD)

        set_waiting_mock = self._run(monkeypatch, tmp_path, owner, check_mock)

        set_waiting_mock.assert_called_once()
        assert set_waiting_mock.call_args.args[0] == [1]

    def test_alive_owner_skips_submission(self, tmp_path, monkeypatch):
        owner = controller_liveness.JobOwnerRecord(pid=100,
                                                   pid_started_at=1.0,
                                                   server_id=None)
        check_mock = mock.MagicMock(
            return_value=controller_liveness.ControllerLiveness.ALIVE)

        set_waiting_mock = self._run(monkeypatch, tmp_path, owner, check_mock)

        set_waiting_mock.assert_called_once()
        assert set_waiting_mock.call_args.args[0] == []

    def test_unknown_owner_fails_closed_skips_submission(
            self, tmp_path, monkeypatch):
        owner = controller_liveness.JobOwnerRecord(pid=100,
                                                   pid_started_at=1.0,
                                                   server_id=None)
        check_mock = mock.MagicMock(
            return_value=controller_liveness.ControllerLiveness.UNKNOWN)

        set_waiting_mock = self._run(monkeypatch, tmp_path, owner, check_mock)

        assert set_waiting_mock.call_args.args[0] == []

    def test_no_owner_record_allows_submission_without_consulting_provider(
            self, tmp_path, monkeypatch):
        """No job_info row for this job at all (fresh submission): the
        provider is never consulted, since there's no owner to check."""
        check_mock = mock.MagicMock()

        set_waiting_mock = self._run(monkeypatch, tmp_path, None, check_mock)

        check_mock.assert_not_called()
        assert set_waiting_mock.call_args.args[0] == [1]

    def test_pid_none_owner_allows_submission_without_consulting_provider(
            self, tmp_path, monkeypatch):
        """A job_info row exists but no controller was ever stamped (pid is
        None): this must submit without consulting the provider, same as no
        owner record at all. This is master's shortcut for an unclaimed job
        -- restoring it guarantees a buggy plugin provider can never strand
        a brand-new job in its one-shot PENDING transition."""
        owner = controller_liveness.JobOwnerRecord(pid=None,
                                                   pid_started_at=None,
                                                   server_id=None)
        check_mock = mock.MagicMock()

        set_waiting_mock = self._run(monkeypatch, tmp_path, owner, check_mock)

        check_mock.assert_not_called()
        assert set_waiting_mock.call_args.args[0] == [1]
