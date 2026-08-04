"""Unit tests for the by-id managed-job accessible-workspace gate.

Covers ``jobs_core._reject_inaccessible_job_ids``, the read/cancel-side of the
per-resource workspace chokepoint for managed jobs. It reuses the job queue
(already accessible-workspace filtered) so a job in an inaccessible workspace
is indistinguishable from a nonexistent one.
"""

from unittest import mock

import pytest

from sky.jobs.server import core as jobs_core


def _record(job_id):
    rec = mock.MagicMock()
    rec.job_id = job_id
    return rec


class TestRejectInaccessibleJobIds:

    def test_empty_is_noop(self):
        with mock.patch.object(jobs_core, 'queue_v2_api') as mock_queue:
            jobs_core._reject_inaccessible_job_ids(None)
            jobs_core._reject_inaccessible_job_ids([])
        mock_queue.assert_not_called()

    def test_all_accessible_does_not_raise(self):
        with mock.patch.object(jobs_core,
                               'queue_v2_api',
                               return_value=([_record(1),
                                              _record(2)], 2, {}, 2)):
            jobs_core._reject_inaccessible_job_ids([1, 2])

    def test_inaccessible_job_rejected(self):
        # The queue (accessible-ws filtered) returns only job 1, so job 2 is
        # treated as not-found.
        with mock.patch.object(jobs_core,
                               'queue_v2_api',
                               return_value=([_record(1)], 1, {}, 1)):
            with pytest.raises(ValueError) as exc_info:
                jobs_core._reject_inaccessible_job_ids([1, 2])
        assert '2' in str(exc_info.value)

    def test_queries_queue_all_users(self):
        # Uses accessible-ws (all_users=True) semantics, not owner-only, so a
        # member can act on a shared-workspace job owned by someone else.
        with mock.patch.object(jobs_core,
                               'queue_v2_api',
                               return_value=([_record(7)], 1, {}, 1)) as mq:
            jobs_core._reject_inaccessible_job_ids([7])
        assert mq.call_args.kwargs['all_users'] is True
        assert mq.call_args.kwargs['job_ids'] == [7]
