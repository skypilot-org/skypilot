"""Unit tests for consolidation mode controller startup gate.

Tests the mechanism that prevents in-request controller startup in consolidation
mode (sky/jobs/scheduler.py::maybe_start_controllers with from_scheduler=True).
The gate is critical: it ensures only the leader-elected refresh daemon owns
the controller pool, preventing split-brain during rolling updates and safely
delegating pool maintenance to a single source.
"""
from unittest import mock

import pytest

from sky.jobs import scheduler
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.skylet import events
from sky.utils import controller_utils


@pytest.fixture
def _gate_env(tmp_path):
    """Helper fixture for hermetic consolidation-mode test environment.

    Returns a context manager (use in a 'with' statement) that patches all the
    necessary mocks for testing maybe_start_controllers in both consolidation
    and non-consolidation modes.
    """

    class _GateEnv:  # pylint: disable=missing-class-docstring

        def __init__(self, consolidation: bool):
            self.consolidation = consolidation
            self.tmp_path = tmp_path
            self._patchers: list = []

        def _start(self, *args, **kwargs):
            """Start a mock.patch.object(...) and track it for teardown."""
            patcher = mock.patch.object(*args, **kwargs)
            # Append before start() so a failed start still lets any prior
            # patchers get stopped by __exit__.
            self._patchers.append(patcher)
            return patcher.start()

        def __enter__(self):
            # Patch start_controller to prevent real spawning
            self.start_controller_mock = self._start(scheduler,
                                                     'start_controller')

            # Patch get_alive_controllers to return 0 (deterministic)
            self.get_alive_mock = self._start(scheduler,
                                              'get_alive_controllers',
                                              return_value=0)

            # Patch controller_utils.get_number_of_jobs_controllers to return 1
            self.get_number_mock = self._start(controller_utils,
                                               'get_number_of_jobs_controllers',
                                               return_value=1)

            # Redirect PID lock and path to tmp_path
            lock_path = self.tmp_path / 'controller.lock'
            pid_path = self.tmp_path / 'controller.pid'
            self.lock_patch = self._start(scheduler, 'JOB_CONTROLLER_PID_LOCK',
                                          str(lock_path))
            self.pid_patch = self._start(scheduler, 'JOB_CONTROLLER_PID_PATH',
                                         str(pid_path))

            # Patch consolidation mode
            self.consolidation_patch = self._start(
                managed_job_utils,
                'is_consolidation_mode',
                return_value=self.consolidation)

            # The recovery signal file must always be absent so that tests
            # exercising the gate never depend on real `~/.sky` state: the
            # gate is `from_scheduler AND is_consolidation_mode()` and must
            # not lean on this file at all (see
            # test_gate_holds_without_recovery_signal_file). Point the
            # constant at a path inside a directory we deliberately do NOT
            # create, so os.path.exists() is guaranteed False unless a test
            # opts in via self.signal_file_path.
            signal_dir = self.tmp_path / 'nosignal'
            self.signal_file_path = signal_dir / 'signal'
            # sky/jobs/scheduler.py does `from sky.skylet import constants`,
            # so the attribute must be patched on the `sky.skylet.constants`
            # module object for the patch to be visible there.
            self.signal_file_patch = self._start(
                skylet_constants, 'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                str(self.signal_file_path))

            # For non-consolidation mode, we need to patch the wheel hash logic
            if not self.consolidation:
                current_hash = self.tmp_path / 'current_sky_wheel_hash'
                current_hash.write_text('hash123')
                self.hash_patch = self._start(scheduler, 'CURRENT_HASH',
                                              str(current_hash))
                self.api_stop_mock = self._start(scheduler.sdk, 'api_stop')
                self.reset_jobs_mock = self._start(scheduler.state,
                                                   'reset_jobs_for_recovery')

            return self

        def __exit__(self, *args):
            # Stop in reverse order, and keep going even if one raises so a
            # single bad patcher can't leak the rest into later tests.
            errors = []
            for patcher in reversed(self._patchers):
                try:
                    patcher.stop()
                except Exception as e:  # pylint: disable=broad-except
                    errors.append(e)
            if errors:
                raise errors[0]

    return _GateEnv


class TestConsolidationModeGate:
    """Tests for the consolidation mode controller startup gate."""

    def test_consolidation_from_scheduler_does_not_start_controller(
            self, _gate_env):  # pylint: disable=invalid-name
        """In consolidation mode with from_scheduler=True, skip startup."""
        with _gate_env(consolidation=True) as env:
            scheduler.maybe_start_controllers(from_scheduler=True)

            # Verify start_controller was NOT called
            env.start_controller_mock.assert_not_called()
            # Verify we never checked pool sizing
            env.get_alive_mock.assert_not_called()
            env.get_number_mock.assert_not_called()

    def test_non_consolidation_from_scheduler_starts_controller(
            self, _gate_env):  # pylint: disable=invalid-name
        """Non-consolidation mode with from_scheduler=True starts controller.

        This guards the jobs-controller-VM path where startup is not delegated.
        """
        with _gate_env(consolidation=False) as env:
            scheduler.maybe_start_controllers(from_scheduler=True)

            # Verify start_controller WAS called
            env.start_controller_mock.assert_called_once()

    def test_consolidation_daemon_path_starts_controller(self, _gate_env):  # pylint: disable=invalid-name
        """With from_scheduler=False, must start controller in consolidation mode.

        CRITICAL: The gate checks from_scheduler, not just consolidation mode.
        """
        with _gate_env(consolidation=True) as env:
            scheduler.maybe_start_controllers(from_scheduler=False)

            # Verify start_controller WAS called (daemon path must work)
            env.start_controller_mock.assert_called_once()

    def test_gate_holds_without_recovery_signal_file(self, _gate_env):  # pylint: disable=invalid-name
        """Gate checks consolidation_mode(), not signal file.

        The `_gate_env` fixture always points
        PERSISTENT_RUN_RESTARTING_SIGNAL_FILE at a path that does not exist
        (see fixture docstring/comments), so simply relying on it here proves
        the gate does not lean on the signal file: the old gate was
        `from_scheduler AND is_consolidation_mode() AND
        os.path.exists(signal_file)`, which would have let this call fall
        through to start_controller() once the file is absent. The new gate
        must still short-circuit.
        """
        with _gate_env(consolidation=True) as env:
            scheduler.maybe_start_controllers(from_scheduler=True)

            # Must not start controller even though signal file doesn't exist
            env.start_controller_mock.assert_not_called()

    def test_gate_short_circuits_before_pool_sizing(self, _gate_env):  # pylint: disable=invalid-name
        """Gate short-circuits before calling pool-sizing functions."""
        with _gate_env(consolidation=True) as env:
            scheduler.maybe_start_controllers(from_scheduler=True)

            # Verify these were never called (would mean we got past the gate)
            env.get_alive_mock.assert_not_called()
            env.get_number_mock.assert_not_called()

    def test_submit_jobs_still_marks_waiting_but_starts_no_controller(
            self, _gate_env, tmp_path, monkeypatch):  # pylint: disable=invalid-name
        """submit_jobs calls scheduler_set_waiting but skips start."""
        # Create temp files for submit_jobs
        dag_path = tmp_path / 'dag.yaml'
        user_path = tmp_path / 'user.yaml'
        env_path = tmp_path / 'env.sh'
        dag_path.touch()
        user_path.touch()
        env_path.touch()

        # Ensure SKYPILOT_CONFIG is unset
        monkeypatch.delenv('SKYPILOT_CONFIG', raising=False)

        with _gate_env(consolidation=True) as env:
            with mock.patch.object(scheduler.state,
                                   'get_job_controller_process',
                                   return_value=None), \
                 mock.patch.object(scheduler.state,
                                   'scheduler_set_waiting') as set_waiting_mock:
                scheduler.submit_jobs([1],
                                      str(dag_path),
                                      str(user_path),
                                      str(env_path),
                                      priority=0)

                # Verify scheduler_set_waiting WAS called (state moved to WAITING)
                assert set_waiting_mock.called
                # Verify start_controller was NOT called
                env.start_controller_mock.assert_not_called()


class TestManagedJobEventDaemonPath:
    """Cheap tripwire for the daemon-side caller of maybe_start_controllers.

    ``ManagedJobEvent._run`` is one of the callers that must remain
    ungated in consolidation mode -- unlike ``submit_jobs``'s
    ``from_scheduler=True`` in-request path, this is the periodic skylet
    tick that self-heals the controller pool. Guards against someone later
    adding a gate to this path, or flipping ``from_scheduler``'s default.
    """

    def test_daemon_event_path_starts_controller_uncgated(self):  # pylint: disable=invalid-name
        event = events.ManagedJobEvent()
        with mock.patch.object(
                events.managed_job_utils,
                'update_managed_jobs_statuses') as update_mock, \
                mock.patch.object(
                    events.scheduler,
                    'maybe_start_controllers') as start_mock, \
                mock.patch.object(
                    events.managed_job_utils,
                    'is_consolidation_mode',
                    return_value=True):
            event._run()  # pylint: disable=protected-access

        update_mock.assert_called_once()
        start_mock.assert_called_once()
        # from_scheduler must be absent (defaults to False) or explicitly
        # falsy -- the daemon path must never claim to be the in-request
        # scheduler path.
        assert not start_mock.call_args.kwargs.get('from_scheduler', False), (
            'ManagedJobEvent._run must call maybe_start_controllers without '
            'from_scheduler=True; that flag is reserved for the in-request '
            'submission path and gates controller startup in consolidation '
            'mode')
