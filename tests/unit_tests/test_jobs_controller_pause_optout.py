"""The jobs controller launch must not be pausable.

``_ensure_controller_up`` runs inside the enclosing ``jobs.launch`` request,
which goes on to create job IDs and submit work. A pause re-queues that whole
request, so the controller launch opts out. It is also the launch most exposed
to the opt-out mattering: it runs with the Kueue queue name removed, so the
admission pause never applied to it, and the scheduled-but-still-starting pause
is the first one that could.
"""
from unittest import mock

from sky.jobs.server import core as jobs_core
from sky.utils import controller_utils
from sky.utils import execution_pause


def _run_ensure_controller_up(observed):
    """Drive _ensure_controller_up with everything around launch() stubbed."""

    def _fake_launch(*args, **kwargs):
        del args, kwargs  # unused
        observed['pause_allowed'] = execution_pause.pause_allowed()
        return (None, None)

    with mock.patch.object(controller_utils, 'get_controller_resources',
                           return_value=set()), \
         mock.patch.object(controller_utils, 'controller_only_vars_to_fill',
                           return_value={}), \
         mock.patch.object(jobs_core.common_utils, 'fill_template'), \
         mock.patch.object(jobs_core.task_lib.Task, 'from_yaml',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.execution, 'launch',
                           side_effect=_fake_launch), \
         mock.patch.object(jobs_core.backend_utils, 'is_controller_accessible',
                           return_value=mock.MagicMock()):
        jobs_core._ensure_controller_up(
            controller_utils.Controllers.JOBS_CONTROLLER)


def test_controller_launch_runs_with_pause_disallowed():
    observed = {}
    _run_ensure_controller_up(observed)
    assert observed.get('pause_allowed') is False, (
        'the jobs controller launch must run under disallow_pause(): a pause '
        're-queues the enclosing jobs.launch request')


def test_the_opt_out_does_not_leak_past_the_controller_launch():
    """Only the controller launch opts out; the worker process must be back to
    pausable afterwards, or every later launch it runs would hold its worker."""
    assert execution_pause.pause_allowed() is True
    _run_ensure_controller_up({})
    assert execution_pause.pause_allowed() is True
