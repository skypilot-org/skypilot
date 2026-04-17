"""Unit tests for sky.jobs.scheduler's HA + consolidation signaling.

Covers:
- _signal_controller_start_needed() touches the wake-up file.
- submit_jobs() in HA+consolidation mode signals the daemon and does NOT
  call maybe_start_controllers() or controller_process_alive().
- submit_jobs() outside HA keeps the direct-start path.
- The submit_jobs() HA path is robust to a false-positive from a local
  controller_process_alive() (PID-collision guard — see the plan's
  "must-fix #1").
"""
import pathlib
import tempfile
from unittest import mock

from sky.jobs import scheduler
from sky.skylet import constants as skylet_constants


def _write_dummy_yaml(path: pathlib.Path, content: str = 'name: t\n') -> None:
    path.write_text(content, encoding='utf-8')


class TestSignalControllerStartNeeded:

    def test_touch_creates_signal_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)):
                scheduler._signal_controller_start_needed()
                assert signal_file.exists()

    def test_touch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            signal_file.touch()
            original_mtime = signal_file.stat().st_mtime
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)):
                # Multiple concurrent submitters coalescing to one wake.
                scheduler._signal_controller_start_needed()
                scheduler._signal_controller_start_needed()
            # File should still exist (the point is: no crash, no duplicate
            # signals to confuse the consumer).
            assert signal_file.exists()
            # touch() bumps mtime; we don't assert equality — the contract is
            # just "file present, no exception raised."
            del original_mtime

    def test_failure_is_non_fatal(self, caplog):
        # Point at an unwritable path; helper must log and swallow.
        with mock.patch.object(skylet_constants, 'CONTROLLER_START_SIGNAL_FILE',
                               '/proc/should_not_be_writable/signal'):
            # Should not raise.
            scheduler._signal_controller_start_needed()


class TestSubmitJobsHaBranching:
    """submit_jobs must signal leader (not start locally) in HA consolidation.

    The full submit_jobs() touches the DB and file system; we mock all
    side-effects and verify only the branch decision + signal emission.
    """

    def _run_submit_jobs(self, tmpdir: str) -> None:
        # Minimal plausible input files.
        tmp = pathlib.Path(tmpdir)
        dag_yaml = tmp / 'dag.yaml'
        user_yaml = tmp / 'user.yaml'
        env_file = tmp / 'env'
        _write_dummy_yaml(dag_yaml)
        _write_dummy_yaml(user_yaml)
        env_file.write_text('', encoding='utf-8')
        scheduler.submit_jobs(job_ids=[1],
                              dag_yaml_path=str(dag_yaml),
                              original_user_yaml_path=str(user_yaml),
                              env_file_path=str(env_file),
                              priority=0)

    def test_ha_consolidation_signals_daemon_and_skips_local_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_consolidation_mode', return_value=True), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_ha_enabled', return_value=True), \
                 mock.patch('sky.jobs.scheduler.state.scheduler_set_waiting'), \
                 mock.patch('sky.jobs.scheduler.maybe_start_controllers'
                           ) as mock_start, \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.controller_process_alive') as mock_alive, \
                 mock.patch('sky.jobs.scheduler.state.get_job_controller_process',
                           return_value=None):
                self._run_submit_jobs(tmpdir)
                # Signal file was written; local start was NOT called.
                assert signal_file.exists()
                mock_start.assert_not_called()
                # Local PID pre-check skipped (this is must-fix #1).
                mock_alive.assert_not_called()

    def test_ha_skips_pid_collision_false_positive(self):
        """Even if controller_process_alive would incorrectly return True
        (PID collision on a non-leader pod), the HA path must not consult
        it, so submission is not silently dropped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_consolidation_mode', return_value=True), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_ha_enabled', return_value=True), \
                 mock.patch('sky.jobs.scheduler.state.scheduler_set_waiting'
                           ) as mock_set_waiting, \
                 mock.patch('sky.jobs.scheduler.maybe_start_controllers'), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.controller_process_alive', return_value=True), \
                 mock.patch('sky.jobs.scheduler.state.get_job_controller_process',
                           return_value=mock.MagicMock()):
                self._run_submit_jobs(tmpdir)
                # Job was still submitted despite the (bogus) "alive" signal.
                args, _ = mock_set_waiting.call_args
                assert args[0] == [1]

    def test_single_replica_consolidation_direct_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_consolidation_mode', return_value=True), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_ha_enabled', return_value=False), \
                 mock.patch('sky.jobs.scheduler.state.scheduler_set_waiting'), \
                 mock.patch('sky.jobs.scheduler.maybe_start_controllers'
                           ) as mock_start, \
                 mock.patch('sky.jobs.scheduler.state.get_job_controller_process',
                           return_value=None):
                self._run_submit_jobs(tmpdir)
                # Direct start is still used; signal file NOT touched.
                mock_start.assert_called_once_with(from_scheduler=True)
                assert not signal_file.exists()

    def test_non_consolidation_direct_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / '.controller_start_needed'
            with mock.patch.object(skylet_constants,
                                   'CONTROLLER_START_SIGNAL_FILE',
                                   str(signal_file)), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_consolidation_mode', return_value=False), \
                 mock.patch('sky.jobs.scheduler.managed_job_utils'
                           '.is_ha_enabled', return_value=False), \
                 mock.patch('sky.jobs.scheduler.state.scheduler_set_waiting'), \
                 mock.patch('sky.jobs.scheduler.maybe_start_controllers'
                           ) as mock_start, \
                 mock.patch('sky.jobs.scheduler.state.get_job_controller_process',
                           return_value=None):
                self._run_submit_jobs(tmpdir)
                mock_start.assert_called_once_with(from_scheduler=True)
                assert not signal_file.exists()
