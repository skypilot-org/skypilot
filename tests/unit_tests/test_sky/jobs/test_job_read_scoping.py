"""Unit tests for the by-id managed-job accessible-workspace gate.

Covers ``jobs_core._reject_inaccessible_job_ids``, the read/cancel-side of the
per-resource workspace chokepoint for managed jobs. It applies only in
consolidation mode (where the API server shares the jobs-state DB); it reads
each job's workspace directly from managed-jobs state and checks it against the
caller's readable workspaces.
"""

from unittest import mock

import pytest

from sky.jobs.server import core as jobs_core


def _consolidation(value):
    return mock.patch.object(jobs_core.managed_job_utils,
                             'is_consolidation_mode',
                             return_value=value)


def _accessible(names):
    return mock.patch.object(jobs_core.workspaces_core,
                             'get_accessible_workspace_names',
                             return_value=set(names))


def _workspaces(mapping):
    # get_workspace(job_id) -> workspace; unknown ids resolve to 'default'.
    return mock.patch.object(
        jobs_core.managed_job_state,
        'get_workspace',
        side_effect=lambda jid: mapping.get(jid, 'default'))


class TestRejectInaccessibleJobIds:

    def test_empty_is_noop(self):
        with _accessible(['ws1']) as acc, _workspaces({}) as gw:
            jobs_core._reject_inaccessible_job_ids(None)
            jobs_core._reject_inaccessible_job_ids([])
        acc.assert_not_called()
        gw.assert_not_called()

    def test_all_accessible_does_not_raise(self):
        with _consolidation(True), _accessible(['ws1']), \
                _workspaces({1: 'ws1', 2: 'ws1'}):
            jobs_core._reject_inaccessible_job_ids([1, 2])  # does not raise

    def test_inaccessible_job_rejected(self):
        # Job 2 lives in a workspace the caller cannot read -> not-found.
        with _consolidation(True), _accessible(['ws1']), \
                _workspaces({1: 'ws1', 2: 'private'}):
            with pytest.raises(ValueError) as exc_info:
                jobs_core._reject_inaccessible_job_ids([1, 2])
        assert '2' in str(exc_info.value)
        assert '1' not in str(exc_info.value)

    def test_reads_state_not_controller(self):
        # Must resolve the workspace from managed-jobs state, never via the
        # controller-dependent queue path.
        with _consolidation(True), _accessible(['ws1']), \
                _workspaces({7: 'ws1'}) as gw:
            with mock.patch.object(jobs_core, 'queue_v2_api') as mock_queue:
                jobs_core._reject_inaccessible_job_ids([7])
        gw.assert_called_once_with(7)
        mock_queue.assert_not_called()

    def test_skipped_in_non_consolidation_mode(self):
        # Non-consolidation: the jobs-state DB is on a separate controller the
        # API server cannot read here, so the check is skipped -- even a job in
        # an inaccessible workspace is not rejected and no DB read happens.
        with _consolidation(False), _accessible(['ws1']), \
                _workspaces({1: 'private'}) as gw:
            jobs_core._reject_inaccessible_job_ids([1])  # does not raise
        gw.assert_not_called()
