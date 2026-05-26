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

from unittest import mock

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


# ---------------------------------------------------------------------------
# ``_STATE_FILTER_TO_SLURM_STATE`` coverage of terminal states.
# ---------------------------------------------------------------------------
#
# ``_query_instances_v1`` iterates the squeue ``--states`` filter set to
# discover the current state of a job within ``MinJobAge`` (default 300s).
# Terminal states beyond plain ``FAILED`` — TIMEOUT, OUT_OF_MEMORY,
# DEADLINE, PREEMPTED, BOOT_FAIL, REVOKED — each surface under their own
# filter; ``--states failed`` does NOT include them. Without entries for
# all of them in the filter map, a walltime-killed or OOM-killed job goes
# invisible to the squeue path and the controller misclassifies the
# cluster as preempted.


@pytest.mark.parametrize('filter_name,slurm_state', [
    ('timeout', 'TIMEOUT'),
    ('out_of_memory', 'OUT_OF_MEMORY'),
    ('deadline', 'DEADLINE'),
    ('preempted', 'PREEMPTED'),
    ('boot_fail', 'BOOT_FAIL'),
    ('revoked', 'REVOKED'),
])
def test_state_filter_includes_terminal_user_and_infra_states(
        filter_name, slurm_state):
    """Each terminal state must have a squeue filter entry.

    Regression for the TIMEOUT-as-preemption bug: a job that hits
    ``TIMEOUT`` within MinJobAge was invisible to the squeue path,
    sacct fallback was gated on ``not non_terminated_only``, and the
    controller misclassified as preempted.
    """
    assert filter_name in slurm_instance._STATE_FILTER_TO_SLURM_STATE, (
        f'{filter_name!r} must be a valid filter so squeue surfaces '
        f'{slurm_state} jobs within MinJobAge.')
    assert slurm_instance._STATE_FILTER_TO_SLURM_STATE[filter_name] == (
        slurm_state)


def _make_mock_client(query_jobs_return=None, sacct_return=None):
    """Build a SlurmClient mock for query_instances tests."""
    client = mock.MagicMock()
    if query_jobs_return is None:
        query_jobs_return = {}

    def _query_jobs(_name, filters):
        return query_jobs_return.get(filters[0], [])

    client.query_jobs.side_effect = _query_jobs
    client.get_job_nodes.return_value = ([], 0)
    client.get_job_reason.return_value = None
    return client, sacct_return


@pytest.mark.parametrize('filter_name,slurm_state', [
    ('timeout', 'TIMEOUT'),
    ('out_of_memory', 'OUT_OF_MEMORY'),
    ('deadline', 'DEADLINE'),
])
def test_query_instances_v1_surfaces_user_terminal_via_squeue(
        filter_name, slurm_state):
    """Within MinJobAge, user-terminal state is read directly from the filter.

    Without the matching filter entry, the squeue loop returned empty for
    TIMEOUT/OUT_OF_MEMORY/etc. and the controller's
    ``_update_cluster_status`` saw ``{}`` — misclassified as preempted
    even though the cluster ran user code to completion.
    """
    client, _ = _make_mock_client(query_jobs_return={filter_name: ['12345']})
    expected = slurm_instance._V1_SLURM_STATE_TO_CLUSTER_STATUS[slurm_state]
    with mock.patch.object(slurm_instance,
                           '_slurm_client_from_provider_config',
                           return_value=client), \
         mock.patch.object(slurm_instance,
                           '_v1_sacct_job_state',
                           return_value=None):
        result = slurm_instance._query_instances_v1(
            'slurm-edge-32', {}, non_terminated_only=True)
    # Single-entry result keyed on job id (no node fanout for terminal jobs).
    assert result == {'12345': (expected, None)}


@pytest.mark.parametrize('filter_name,slurm_state', [
    ('preempted', 'PREEMPTED'),
    ('boot_fail', 'BOOT_FAIL'),
    ('revoked', 'REVOKED'),
])
def test_query_instances_v1_infra_terminal_via_squeue_falls_through(
        filter_name, slurm_state):
    """For infra-terminal states (sky_status=None) with non_terminated_only=True,
    squeue's discovery is suppressed (the caller doesn't want terminated
    instances), and we fall through to sacct which surfaces the most-recent
    parent-row state keyed on the cluster name. This preserves the
    ``cluster_status=None → recovery`` signal."""
    del slurm_state
    client, _ = _make_mock_client(query_jobs_return={filter_name: ['12345']})
    with mock.patch.object(slurm_instance,
                           '_slurm_client_from_provider_config',
                           return_value=client), \
         mock.patch.object(slurm_instance,
                           '_v1_sacct_job_state',
                           return_value='NODE_FAIL'):
        result = slurm_instance._query_instances_v1(
            'slurm-edge-32', {}, non_terminated_only=True)
    assert result == {'slurm-edge-32': (None, 'NODE_FAIL')}


def test_query_instances_v1_sacct_fallback_fires_when_squeue_empty():
    """When squeue returns nothing, sacct is consulted regardless of
    ``non_terminated_only``. The pre-fix code gated the sacct branch on
    ``not non_terminated_only`` — but the standard caller
    (``backend_utils._update_cluster_status``) passes the default
    ``True``, so a job aged past MinJobAge fell through entirely and
    the controller saw ``{}``.
    """
    client, _ = _make_mock_client(query_jobs_return={})
    with mock.patch.object(slurm_instance,
                           '_slurm_client_from_provider_config',
                           return_value=client), \
         mock.patch.object(slurm_instance,
                           '_v1_sacct_job_state',
                           return_value='TIMEOUT'):
        result = slurm_instance._query_instances_v1(
            'slurm-edge-32', {}, non_terminated_only=True)
    assert result == {
        'slurm-edge-32': (status_lib.ClusterStatus.UP, 'TIMEOUT')
    }


def test_query_instances_v1_sacct_returns_none_for_infra_terminal():
    """Infrastructure-terminal states map cluster_status to None via sacct."""
    client, _ = _make_mock_client(query_jobs_return={})
    with mock.patch.object(slurm_instance,
                           '_slurm_client_from_provider_config',
                           return_value=client), \
         mock.patch.object(slurm_instance,
                           '_v1_sacct_job_state',
                           return_value='NODE_FAIL'):
        result = slurm_instance._query_instances_v1(
            'slurm-edge-32', {}, non_terminated_only=True)
    assert result == {'slurm-edge-32': (None, 'NODE_FAIL')}


def test_query_instances_v1_no_sacct_call_when_squeue_has_result():
    """Defense-in-depth: don't waste a sacct call when squeue already
    surfaced the job."""
    client, _ = _make_mock_client(query_jobs_return={'running': ['77']})
    client.get_job_nodes.return_value = (['node-1'], 0)
    sacct_mock = mock.MagicMock(return_value='COMPLETED')
    with mock.patch.object(slurm_instance,
                           '_slurm_client_from_provider_config',
                           return_value=client), \
         mock.patch.object(slurm_instance,
                           '_v1_sacct_job_state',
                           sacct_mock):
        slurm_instance._query_instances_v1(
            'slurm-edge-32', {}, non_terminated_only=True)
    sacct_mock.assert_not_called()
