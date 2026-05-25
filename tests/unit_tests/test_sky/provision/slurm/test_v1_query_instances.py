"""Unit tests for the v1 ``query_instances`` cluster-status mapping.

The mapping in ``_V1_SLURM_STATE_TO_CLUSTER_STATUS`` is a controller
integration point, not just a Slurm-state convention. The managed-jobs
controller decides retry-vs-fail by comparing the cluster status to
``ClusterStatus.UP`` (sky/jobs/controller.py:874-947):

  - ``cluster_status == UP`` AND ``job_status in user_code_failure_states``
    → fail the managed job immediately (user code crashed).
  - ``cluster_status != UP`` → infrastructure failure, take the recovery
    branch and relaunch.

For v1, where the Slurm allocation IS the user job, that distinction has
to come from the Slurm state token. User-code-determined terminations
(``FAILED``/``TIMEOUT``/``OUT_OF_MEMORY``/``DEADLINE``/``SPECIAL_EXIT``,
plus ``COMPLETED`` for symmetry) must map to ``UP``. Real
infrastructure failures (``NODE_FAIL``/``BOOT_FAIL``/``PREEMPTED``/
``REVOKED``) and user cancellation (``CANCELLED``) map to ``None``.

If ``FAILED`` ever regresses to ``None`` again, a managed job whose user
code exits non-zero will be retried indefinitely.
"""
# pylint: disable=protected-access

import pytest

from sky.provision.slurm import instance as slurm_instance
from sky.utils import status_lib


@pytest.mark.parametrize('state', [
    'PENDING',
    'CONFIGURING',
    'RESV_DEL_HOLD',
    'REQUEUED',
    'REQUEUE_HOLD',
    'REQUEUE_FED',
    'RESIZING',
])
def test_pending_states_map_to_init(state):
    assert slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS[state] is (
        status_lib.ClusterStatus.INIT)


@pytest.mark.parametrize('state', [
    'RUNNING',
    'COMPLETING',
    'SIGNALING',
    'STAGE_OUT',
    'SUSPENDED',
])
def test_running_states_map_to_up(state):
    assert slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS[state] is (
        status_lib.ClusterStatus.UP)


@pytest.mark.parametrize('state', [
    'COMPLETED',
    'FAILED',
    'TIMEOUT',
    'OUT_OF_MEMORY',
    'DEADLINE',
    'SPECIAL_EXIT',
])
def test_user_code_terminal_states_map_to_up(state):
    """User-code-determined terminations must report cluster as UP.

    Otherwise the controller's user-code-failure branch
    (controller.py:874-947) never fires and the managed job is retried
    indefinitely. This is the integration contract — do not change
    without also updating ``sky/jobs/controller.py``.
    """
    assert slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS[state] is (
        status_lib.ClusterStatus.UP), (
            f'{state} must map to UP so the controller distinguishes '
            'user-code failure from infrastructure preemption.')


@pytest.mark.parametrize('state', [
    'NODE_FAIL',
    'BOOT_FAIL',
    'PREEMPTED',
    'REVOKED',
    'CANCELLED',
])
def test_infrastructure_and_cancelled_states_map_to_none(state):
    """Real infrastructure failures map to None (cluster gone).

    Tells the controller "cluster preempted/failed" so it takes the
    recovery path. CANCELLED is included because the controller has a
    separate cancellation handler and we want query_instances to
    report the cluster as gone post-cancel.
    """
    assert (slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS[state] is
            None), (f'{state} must map to None so the controller takes the '
                    'recovery / cleanup path.')


def test_no_unknown_states_in_table():
    """Every key in the table is a documented Slurm state.

    Sanity-check that we haven't accidentally added a typo'd entry that
    would silently default to ``None`` on a real Slurm response.
    """
    documented = {
        # Pending-like
        'PENDING',
        'CONFIGURING',
        'RESV_DEL_HOLD',
        'REQUEUED',
        'REQUEUE_HOLD',
        'REQUEUE_FED',
        'RESIZING',
        # Running-like
        'RUNNING',
        'COMPLETING',
        'SIGNALING',
        'STAGE_OUT',
        'SUSPENDED',
        # User-terminal
        'COMPLETED',
        'FAILED',
        'TIMEOUT',
        'OUT_OF_MEMORY',
        'DEADLINE',
        'SPECIAL_EXIT',
        # Infra-terminal
        'NODE_FAIL',
        'BOOT_FAIL',
        'PREEMPTED',
        'REVOKED',
        'CANCELLED',
    }
    assert set(slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS) == documented
