"""Tests for sky.utils.debug_utils module."""
import datetime
import json
import os
import posixpath
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set
from unittest import mock
import zipfile

import pytest

from sky import clouds
from sky import exceptions
from sky.server import constants as server_constants
from sky.server.requests import request_names
from sky.skylet import constants as skylet_constants
from sky.utils import command_runner
from sky.utils import debug_dump_helpers
from sky.utils import debug_utils
from sky.utils import status_lib


def _make_context(
    request_ids: Optional[Set[str]] = None,
    cluster_names: Optional[Set[str]] = None,
    managed_job_ids: Optional[Set[int]] = None,
    errors: Optional[List[Dict[str, str]]] = None,
    request_ids_via_job: Optional[Set[str]] = None,
    request_ids_via_cluster: Optional[Set[str]] = None,
) -> debug_utils.DebugDumpContext:
    """Helper to create a DebugDumpContext."""
    return debug_utils.DebugDumpContext(
        request_ids=request_ids or set(),
        cluster_names=cluster_names or set(),
        managed_job_ids=managed_job_ids or set(),
        request_ids_via_job=request_ids_via_job or set(),
        request_ids_via_cluster=request_ids_via_cluster or set(),
        errors=errors if errors is not None else [],
    )


def _make_request(
    request_id: str = 'req-123',
    cluster_name: Optional[str] = None,
    request_body: Any = None,
    return_value: Any = None,
    created_at: Optional[float] = None,
    finished_at: Optional[float] = None,
    status: Optional[str] = 'RUNNING',
    name: str = 'sky.launch',
    status_msg: Optional[str] = None,
    user_id: str = 'user-1',
    schedule_type: Optional[str] = None,
) -> SimpleNamespace:
    """Helper to create a mock Request object."""
    return SimpleNamespace(
        request_id=request_id,
        cluster_name=cluster_name,
        request_body=request_body,
        return_value=return_value,
        created_at=created_at or time.time(),
        finished_at=finished_at,
        status=SimpleNamespace(value=status) if status else None,
        name=name,
        status_msg=status_msg,
        user_id=user_id,
        schedule_type=(SimpleNamespace(
            value=schedule_type) if schedule_type else None),
        get_error=lambda: None,
    )


# ---------------------------------------------------------------------------
# Tests for _epoch_to_human
# ---------------------------------------------------------------------------
class TestEpochToHuman:

    def test_valid_epoch_returns_iso_format(self):
        """A valid epoch timestamp should return an ISO format string."""
        epoch = 1700000000.0  # 2023-11-14T22:13:20
        result = debug_dump_helpers.epoch_to_human(epoch)
        assert result is not None
        # Should be parseable as a datetime
        datetime.datetime.fromisoformat(result)

    def test_none_returns_none(self):
        """None input should return None."""
        assert debug_dump_helpers.epoch_to_human(None) is None

    def test_zero_returns_valid_date(self):
        """Epoch 0 should return a valid date (1970-01-01 UTC)."""
        result = debug_dump_helpers.epoch_to_human(0)
        assert result is not None
        # The result is in local time, so convert back to UTC to check.
        dt = datetime.datetime.fromisoformat(result)
        utc_dt = dt.astimezone(datetime.timezone.utc)
        assert utc_dt.year == 1970

    def test_current_time(self):
        """Current time epoch should return a valid ISO date."""
        result = debug_dump_helpers.epoch_to_human(time.time())
        assert result is not None
        datetime.datetime.fromisoformat(result)


# ---------------------------------------------------------------------------
# Tests for _get_requests_from_clusters
# ---------------------------------------------------------------------------
class TestGetRequestsFromClusters:

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_finds_request_ids_for_clusters(self, mock_get_tasks):
        """Given cluster names, the function should collect request IDs."""
        mock_get_tasks.return_value = [
            _make_request(request_id='req-1'),
            _make_request(request_id='req-2'),
        ]
        ctx = _make_context(cluster_names={'my-cluster'})

        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == {'req-1', 'req-2'}
        mock_get_tasks.assert_called_once()
        call_args = mock_get_tasks.call_args
        task_filter = call_args[0][0]
        assert task_filter.cluster_names == ['my-cluster']
        assert task_filter.fields == ['request_id']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_multiple_clusters(self, mock_get_tasks):
        """Each cluster name should be queried separately."""
        mock_get_tasks.side_effect = [
            [_make_request(request_id='req-a')],
            [_make_request(request_id='req-b')],
        ]
        ctx = _make_context(cluster_names={'cluster-1', 'cluster-2'})

        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == {'req-a', 'req-b'}
        assert mock_get_tasks.call_count == 2

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_empty_cluster_names_is_noop(self, mock_get_tasks):
        """Empty cluster_names should not trigger any DB call."""
        ctx = _make_context(cluster_names=set())

        debug_utils._get_requests_from_clusters(ctx)

        mock_get_tasks.assert_not_called()
        assert ctx['request_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_db_failure_logs_warning(self, mock_get_tasks):
        """DB failure should log a warning but not crash."""
        mock_get_tasks.side_effect = RuntimeError('DB connection failed')
        ctx = _make_context(cluster_names={'my-cluster'})

        # Should not raise
        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_deduplicates_request_ids(self, mock_get_tasks):
        """Duplicate request IDs across clusters should be deduplicated."""
        mock_get_tasks.side_effect = [
            [_make_request(request_id='req-dup')],
            [_make_request(request_id='req-dup')],
        ]
        ctx = _make_context(cluster_names={'cluster-1', 'cluster-2'})

        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == {'req-dup'}


# ---------------------------------------------------------------------------
# Tests for _get_requests_from_managed_jobs
# ---------------------------------------------------------------------------
class TestGetRequestsFromManagedJobs:

    MOCK_QUEUE_V2 = 'sky.jobs.server.core.queue_v2'

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_finds_requests_by_job_id(self, mock_get_tasks, _mock_queue):
        """Should find requests whose body has a matching job_id."""
        body_with_job_id = SimpleNamespace(job_id=42, job_ids=None)
        mock_get_tasks.return_value = [
            _make_request(request_id='req-j1',
                          request_body=body_with_job_id,
                          name='jobs.launch'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-j1' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_finds_requests_by_job_ids_list(self, mock_get_tasks, _mock_queue):
        """Should find requests whose body has matching job_ids list."""
        body_with_job_ids = SimpleNamespace(job_id=None, job_ids=[10, 20, 30])
        mock_get_tasks.return_value = [
            _make_request(request_id='req-j2',
                          request_body=body_with_job_ids,
                          name='jobs.cancel'),
        ]
        ctx = _make_context(managed_job_ids={20})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-j2' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_skips_non_matching_job_ids(self, mock_get_tasks, _mock_queue):
        """Requests with unrelated job IDs should not be collected."""
        body = SimpleNamespace(job_id=99, job_ids=None)
        mock_get_tasks.return_value = [
            _make_request(request_id='req-j3',
                          request_body=body,
                          name='jobs.launch'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert ctx['request_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_empty_job_ids_is_noop(self, mock_get_tasks):
        """Empty managed_job_ids should not trigger any DB call."""
        ctx = _make_context(managed_job_ids=set())

        debug_utils._get_requests_from_managed_jobs(ctx)

        mock_get_tasks.assert_not_called()
        assert ctx['request_ids'] == set()

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_none_body_is_skipped(self, mock_get_tasks, _mock_queue):
        """Requests with None body should be silently skipped."""
        mock_get_tasks.return_value = [
            _make_request(request_id='req-j4',
                          request_body=None,
                          name='jobs.launch'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert ctx['request_ids'] == set()

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_db_failure_logs_warning(self, mock_get_tasks, _mock_queue):
        """DB failure should log warning but not crash."""
        mock_get_tasks.side_effect = RuntimeError('DB error')
        ctx = _make_context(managed_job_ids={42})

        # Should not raise
        debug_utils._get_requests_from_managed_jobs(ctx)

        assert ctx['request_ids'] == set()

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_filters_by_managed_job_request_names(self, mock_get_tasks,
                                                  _mock_queue):
        """Should query with managed job-related request names."""
        mock_get_tasks.return_value = []
        ctx = _make_context(managed_job_ids={1})

        debug_utils._get_requests_from_managed_jobs(ctx)

        call_args = mock_get_tasks.call_args
        task_filter = call_args[0][0]
        assert task_filter.include_request_names is not None
        assert 'sky.jobs.launch' in task_filter.include_request_names
        assert 'sky.jobs.cancel' in task_filter.include_request_names
        assert 'sky.jobs.logs' in task_filter.include_request_names
        # Queue is read-only, should not be included
        assert 'sky.jobs.queue' not in task_filter.include_request_names

    @mock.patch(MOCK_QUEUE_V2)
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_matches_cancel_by_name(self, mock_get_tasks, mock_queue):
        """Should match cancel requests that target a job by name."""
        mock_queue.return_value = ([{
            'job_id': 42,
            'job_name': 'my-training',
            'user_hash': 'user-abc'
        }], 1, {}, 1)
        body = SimpleNamespace(job_id=None,
                               job_ids=None,
                               name='my-training',
                               all=False,
                               all_users=False)
        mock_get_tasks.return_value = [
            _make_request(request_id='req-cancel-name',
                          request_body=body,
                          name='jobs.cancel'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-cancel-name' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2)
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_matches_cancel_all_users(self, mock_get_tasks, mock_queue):
        """Should match cancel-all-users requests."""
        mock_queue.return_value = ([{
            'job_id': 42,
            'job_name': 'my-job',
            'user_hash': 'user-abc'
        }], 1, {}, 1)
        body = SimpleNamespace(job_id=None,
                               job_ids=None,
                               name=None,
                               all=False,
                               all_users=True)
        mock_get_tasks.return_value = [
            _make_request(request_id='req-cancel-all-users',
                          request_body=body,
                          name='jobs.cancel'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-cancel-all-users' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2)
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_matches_cancel_all_same_user(self, mock_get_tasks, mock_queue):
        """Should match cancel-all if the user owns a target job."""
        mock_queue.return_value = ([{
            'job_id': 42,
            'job_name': 'my-job',
            'user_hash': 'user-abc'
        }], 1, {}, 1)
        body = SimpleNamespace(job_id=None,
                               job_ids=None,
                               name=None,
                               all=True,
                               all_users=False,
                               env_vars={'SKYPILOT_USER_ID': 'user-abc'})
        mock_get_tasks.return_value = [
            _make_request(request_id='req-cancel-all',
                          request_body=body,
                          name='jobs.cancel'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-cancel-all' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2)
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_skips_cancel_all_different_user(self, mock_get_tasks, mock_queue):
        """Should NOT match cancel-all from a different user."""
        mock_queue.return_value = ([{
            'job_id': 42,
            'job_name': 'my-job',
            'user_hash': 'user-abc'
        }], 1, {}, 1)
        body = SimpleNamespace(job_id=None,
                               job_ids=None,
                               name=None,
                               all=True,
                               all_users=False,
                               env_vars={'SKYPILOT_USER_ID': 'user-xyz'})
        mock_get_tasks.return_value = [
            _make_request(request_id='req-cancel-other',
                          request_body=body,
                          name='jobs.cancel'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-cancel-other' not in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_matches_launch_by_return_value(self, mock_get_tasks, _mock_queue):
        """Should match jobs.launch request via return_value.job_id."""
        mock_get_tasks.return_value = [
            _make_request(request_id='req-rv',
                          request_body=SimpleNamespace(job_id=None,
                                                       job_ids=None),
                          return_value={'job_id': 42},
                          name='sky.jobs.launch'),
        ]
        ctx = _make_context(managed_job_ids={42})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-rv' in ctx['request_ids']

    @mock.patch(MOCK_QUEUE_V2, return_value=([], 0, {}, 0))
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_matches_launch_by_return_value_list(self, mock_get_tasks,
                                                 _mock_queue):
        """Should match jobs.launch when return_value.job_id is a list."""
        mock_get_tasks.return_value = [
            _make_request(request_id='req-rv-list',
                          request_body=SimpleNamespace(job_id=None,
                                                       job_ids=None),
                          return_value={'job_id': [42, 43]},
                          name='sky.jobs.launch'),
        ]
        ctx = _make_context(managed_job_ids={43})

        debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-rv-list' in ctx['request_ids']


# ---------------------------------------------------------------------------
# Tests for _get_clusters_from_requests
# ---------------------------------------------------------------------------
class TestGetClustersFromRequests:

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_finds_cluster_names_from_requests(self, mock_get_request):
        """Should collect cluster names from request metadata."""
        mock_get_request.return_value = _make_request(request_id='req-1',
                                                      cluster_name='my-cluster')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_clusters_from_requests(ctx)

        assert 'my-cluster' in ctx['cluster_names']
        mock_get_request.assert_called_once_with('req-1',
                                                 fields=['cluster_name'])

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_skips_none_cluster_name(self, mock_get_request):
        """Requests with None cluster_name should be skipped."""
        mock_get_request.return_value = _make_request(request_id='req-1',
                                                      cluster_name=None)
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_clusters_from_requests(ctx)

        assert ctx['cluster_names'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_skips_none_request(self, mock_get_request):
        """If request is not found (returns None), skip it."""
        mock_get_request.return_value = None
        ctx = _make_context(request_ids={'req-nonexistent'})

        debug_utils._get_clusters_from_requests(ctx)

        assert ctx['cluster_names'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_empty_request_ids_is_noop(self, mock_get_request):
        """Empty request_ids should not trigger any DB call."""
        ctx = _make_context(request_ids=set())

        debug_utils._get_clusters_from_requests(ctx)

        mock_get_request.assert_not_called()
        assert ctx['cluster_names'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_db_failure_logs_warning(self, mock_get_request):
        """DB failure should log warning but not crash."""
        mock_get_request.side_effect = RuntimeError('DB error')
        ctx = _make_context(request_ids={'req-1'})

        # Should not raise
        debug_utils._get_clusters_from_requests(ctx)

        assert ctx['cluster_names'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_multiple_requests_collect_clusters(self, mock_get_request):
        """Multiple requests should collect all distinct cluster names."""
        mock_get_request.side_effect = [
            _make_request(request_id='req-1', cluster_name='cluster-a'),
            _make_request(request_id='req-2', cluster_name='cluster-b'),
            _make_request(request_id='req-3', cluster_name='cluster-a'),
        ]
        ctx = _make_context(request_ids={'req-1', 'req-2', 'req-3'})

        debug_utils._get_clusters_from_requests(ctx)

        assert ctx['cluster_names'] == {'cluster-a', 'cluster-b'}


# ---------------------------------------------------------------------------
# Tests for _get_clusters_from_managed_jobs
# ---------------------------------------------------------------------------
class TestGetClustersFromManagedJobs:

    @mock.patch('sky.utils.debug_utils.common.JOB_CONTROLLER_NAME',
                'sky-jobs-controller-abc123')
    def test_adds_jobs_controller(self):
        """Should add the jobs controller cluster name."""
        ctx = _make_context(managed_job_ids={1})

        debug_utils._get_clusters_from_managed_jobs(ctx)

        assert 'sky-jobs-controller-abc123' in ctx['cluster_names']

    def test_empty_job_ids_is_noop(self):
        """Empty managed_job_ids should not add anything."""
        ctx = _make_context(managed_job_ids=set())

        debug_utils._get_clusters_from_managed_jobs(ctx)

        assert ctx['cluster_names'] == set()


# ---------------------------------------------------------------------------
# Tests for _get_managed_jobs_from_requests
# ---------------------------------------------------------------------------
class TestGetManagedJobsFromRequests:

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_extracts_job_id_from_launch(self, mock_get_request):
        """Should extract job_id from a jobs.launch request body."""
        body = SimpleNamespace(job_id=42, job_ids=None)
        mock_get_request.return_value = _make_request(request_id='req-1',
                                                      request_body=body,
                                                      name='sky.jobs.launch')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert 42 in ctx['managed_job_ids']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_extracts_job_ids_from_cancel(self, mock_get_request):
        """Should extract job_ids list from a jobs.cancel request body."""
        body = SimpleNamespace(job_id=None, job_ids=[10, 20])
        mock_get_request.return_value = _make_request(request_id='req-1',
                                                      request_body=body,
                                                      name='sky.jobs.cancel')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert 10 in ctx['managed_job_ids']
        assert 20 in ctx['managed_job_ids']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_skips_non_managed_job_requests(self, mock_get_request):
        """Should skip requests that are not managed job requests."""
        mock_get_request.return_value = _make_request(
            request_id='req-1',
            request_body=SimpleNamespace(cluster_name='my-cluster'),
            name='sky.launch')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_skips_none_body(self, mock_get_request):
        """Should skip requests with None body."""
        mock_get_request.return_value = _make_request(request_id='req-1',
                                                      request_body=None,
                                                      name='sky.jobs.launch')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == set()

    def test_empty_request_ids_is_noop(self):
        """Empty request_ids should not trigger any DB call."""
        ctx = _make_context(request_ids=set())

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_db_failure_does_not_crash(self, mock_get_request):
        """DB failure should not crash the function."""
        mock_get_request.side_effect = RuntimeError('DB error')
        ctx = _make_context(request_ids={'req-1'})

        # Should not raise
        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_extracts_job_id_from_return_value(self, mock_get_request):
        """Should extract job_id from jobs.launch return_value."""
        mock_get_request.return_value = _make_request(
            request_id='req-1',
            request_body=SimpleNamespace(job_id=None, job_ids=None),
            return_value={'job_id': 42},
            name='sky.jobs.launch')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert 42 in ctx['managed_job_ids']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_extracts_job_ids_list_from_return_value(self, mock_get_request):
        """Should extract list of job_ids from jobs.launch return_value."""
        mock_get_request.return_value = _make_request(
            request_id='req-1',
            request_body=SimpleNamespace(job_id=None, job_ids=None),
            return_value={'job_id': [42, 43]},
            name='sky.jobs.launch')
        ctx = _make_context(request_ids={'req-1'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert 42 in ctx['managed_job_ids']
        assert 43 in ctx['managed_job_ids']


# ---------------------------------------------------------------------------
# Tests for _populate_recent_context
# ---------------------------------------------------------------------------
class TestPopulateRecentContext:

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_includes_recent_requests(self, mock_get_tasks, mock_get_clusters,
                                      mock_queue_v2):
        """Requests within the time window should be included.
        Cluster names are handled by _get_clusters_from_requests."""
        # DB-side finished_after filter returns only recent requests
        recent_request = _make_request(request_id='req-recent')
        mock_get_tasks.return_value = [recent_request]
        mock_get_clusters.return_value = []
        mock_queue_v2.return_value = ([], 0, {}, 0)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert 'req-recent' in ctx['request_ids']

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_passes_finished_after_to_db(self, mock_get_tasks,
                                         mock_get_clusters, mock_queue_v2):
        """Should push time filtering to the DB via finished_after."""
        mock_get_tasks.return_value = []
        mock_get_clusters.return_value = []
        mock_queue_v2.return_value = ([], 0, {}, 0)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=120.0)

        call_args = mock_get_tasks.call_args
        task_filter = call_args[0][0]
        assert task_filter.finished_after is not None
        # finished_after should be approximately now - 120*60
        expected_cutoff = time.time() - (120.0 * 60)
        assert abs(task_filter.finished_after - expected_cutoff) < 5

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_includes_recent_clusters(self, mock_get_tasks, mock_get_clusters,
                                      mock_queue_v2):
        """Clusters with recent status updates should be included."""
        now = time.time()
        mock_get_tasks.return_value = []
        mock_get_clusters.return_value = [
            {
                'name': 'active-cluster',
                'status_updated_at': now - 1800,
                'launched_at': now - 7200,
            },
            {
                'name': 'old-cluster',
                'status_updated_at': now - 100000,
                'launched_at': now - 200000,
            },
        ]
        mock_queue_v2.return_value = ([], 0, {}, 0)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert 'active-cluster' in ctx['cluster_names']
        assert 'old-cluster' not in ctx['cluster_names']

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_includes_recent_managed_jobs(self, mock_get_tasks,
                                          mock_get_clusters, mock_queue_v2):
        """Managed jobs within the time window should be included."""
        now = time.time()
        mock_get_tasks.return_value = []
        mock_get_clusters.return_value = []
        mock_queue_v2.return_value = ([
            {
                'job_id': 1,
                'submitted_at': now - 1800,
                'end_at': None,
            },
            {
                'job_id': 2,
                'submitted_at': now - 100000,
                'end_at': now - 90000,
            },
        ], 2, {}, 2)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert 1 in ctx['managed_job_ids']
        assert 2 not in ctx['managed_job_ids']

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_still_running_requests_included(self, mock_get_tasks,
                                             mock_get_clusters, mock_queue_v2):
        """Requests without finished_at (still running) should be included
        because the DB filter uses (finished_at >= ? OR finished_at IS NULL)."""
        running_request = _make_request(request_id='req-running',
                                        finished_at=None)
        mock_get_tasks.return_value = [running_request]
        mock_get_clusters.return_value = []
        mock_queue_v2.return_value = ([], 0, {}, 0)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert 'req-running' in ctx['request_ids']

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_db_failures_do_not_crash(self, mock_get_tasks, mock_get_clusters,
                                      mock_queue_v2):
        """All DB failures should be caught and not crash the function."""
        mock_get_tasks.side_effect = RuntimeError('requests DB error')
        mock_get_clusters.side_effect = RuntimeError('clusters DB error')
        mock_queue_v2.side_effect = RuntimeError('jobs DB error')

        ctx = _make_context()
        # Should not raise
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert ctx['request_ids'] == set()
        assert ctx['cluster_names'] == set()
        assert ctx['managed_job_ids'] == set()

    @mock.patch('sky.jobs.server.core.queue_v2')
    @mock.patch('sky.utils.debug_utils.global_user_state.get_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_cluster_launched_recently_is_included(self, mock_get_tasks,
                                                   mock_get_clusters,
                                                   mock_queue_v2):
        """Clusters launched recently should be included even if status
        was not updated recently."""
        now = time.time()
        mock_get_tasks.return_value = []
        mock_get_clusters.return_value = [{
            'name': 'newly-launched',
            'status_updated_at': now - 100000,
            'launched_at': now - 1800,
        }]
        mock_queue_v2.return_value = ([], 0, {}, 0)

        ctx = _make_context()
        debug_utils._populate_recent_context(ctx, minutes=60.0)

        assert 'newly-launched' in ctx['cluster_names']


# ---------------------------------------------------------------------------
# Tests for cross-link cycle prevention via provenance sidecars.
#
# Without these guards, a request linked in via a job (e.g. a
# sky.jobs.launch matched on body.name) would re-seed managed_job_ids
# with that request's return_value.job_id, dragging every sibling job
# of a batch launch into the dump. Symmetric story for clusters.
# ---------------------------------------------------------------------------
class TestCrossLinkCycleBreak:

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_request_added_via_job_does_not_reseed_managed_jobs(
            self, mock_get_request):
        """job → request → job cycle is broken."""
        # Simulate: req-from-job was added by _get_requests_from_managed_jobs
        # (e.g. matched on body.name="sb-sweep-8"). Its body.job_id would
        # point at a SIBLING job (B) that shares the task name with our
        # seed job A. Without the guard, B would be added to
        # managed_job_ids.
        body = SimpleNamespace(job_id=999, job_ids=None)
        mock_get_request.return_value = _make_request(request_id='req-from-job',
                                                      request_body=body,
                                                      name='sky.jobs.launch')
        ctx = _make_context(
            request_ids={'req-from-job'},
            request_ids_via_job={'req-from-job'},
            managed_job_ids={42},  # original seed job
        )

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == {42}
        # The guarded request was never even fetched.
        mock_get_request.assert_not_called()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_request_added_via_cluster_does_not_reseed_clusters(
            self, mock_get_request):
        """cluster → request → cluster cycle is broken."""
        # Simulate: req-from-cluster was added by
        # _get_requests_from_clusters. Its cluster_name points at a
        # DIFFERENT cluster than the seed (since the request also
        # touched another cluster). Without the guard, that other
        # cluster would be added to cluster_names.
        mock_get_request.return_value = _make_request(
            request_id='req-from-cluster', cluster_name='other-cluster')
        ctx = _make_context(
            request_ids={'req-from-cluster'},
            request_ids_via_cluster={'req-from-cluster'},
            cluster_names={'seed-cluster'},
        )

        debug_utils._get_clusters_from_requests(ctx)

        assert ctx['cluster_names'] == {'seed-cluster'}
        mock_get_request.assert_not_called()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_unrestricted_request_still_expands_jobs(self, mock_get_request):
        """A user-seeded request (no provenance tag) still expands."""
        body = SimpleNamespace(job_id=42, job_ids=None)
        mock_get_request.return_value = _make_request(request_id='req-seed',
                                                      request_body=body,
                                                      name='sky.jobs.launch')
        # No provenance tag — this came from user input or recent context.
        ctx = _make_context(request_ids={'req-seed'})

        debug_utils._get_managed_jobs_from_requests(ctx)

        assert 42 in ctx['managed_job_ids']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_unrestricted_request_still_expands_clusters(
            self, mock_get_request):
        """A user-seeded request (no provenance tag) still expands."""
        mock_get_request.return_value = _make_request(request_id='req-seed',
                                                      cluster_name='c1')
        ctx = _make_context(request_ids={'req-seed'})

        debug_utils._get_clusters_from_requests(ctx)

        assert 'c1' in ctx['cluster_names']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_requests_from_managed_jobs_tags_provenance(self, mock_get_tasks):
        """Requests added by job-cross-link are tagged."""
        body = SimpleNamespace(job_id=42,
                               job_ids=None,
                               name='task',
                               all_users=False,
                               all=False)
        mock_get_tasks.return_value = [
            _make_request(request_id='req-1',
                          request_body=body,
                          name='sky.jobs.launch'),
        ]
        # queue_v2 must not raise — patch it as a no-op.
        with mock.patch('sky.utils.debug_utils.managed_jobs_core.queue_v2',
                        return_value=([], 0, {}, 0)):
            ctx = _make_context(managed_job_ids={42})
            debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'req-1' in ctx['request_ids']
        assert 'req-1' in ctx['request_ids_via_job']
        assert 'req-1' not in ctx['request_ids_via_cluster']

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_requests_from_clusters_tags_provenance(self, mock_get_tasks):
        """Requests added by cluster-cross-link are tagged."""
        mock_get_tasks.return_value = [
            _make_request(request_id='req-1', cluster_name='c1'),
            _make_request(request_id='req-2', cluster_name='c1'),
        ]
        ctx = _make_context(cluster_names={'c1'})

        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == {'req-1', 'req-2'}
        assert ctx['request_ids_via_cluster'] == {'req-1', 'req-2'}
        assert ctx['request_ids_via_job'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_requests_from_clusters_does_not_tag_preexisting(
            self, mock_get_tasks):
        """A user-seeded request that also matches a cluster scan must
        not inherit the via_cluster tag, otherwise the user's explicit
        request would be wrongly skipped by _get_clusters_from_requests."""
        mock_get_tasks.return_value = [
            _make_request(request_id='user-seeded', cluster_name='c1'),
            _make_request(request_id='new-from-cluster', cluster_name='c1'),
        ]
        # 'user-seeded' was already in request_ids before the helper ran.
        ctx = _make_context(cluster_names={'c1'}, request_ids={'user-seeded'})

        debug_utils._get_requests_from_clusters(ctx)

        assert ctx['request_ids'] == {'user-seeded', 'new-from-cluster'}
        # Only the genuinely new one carries the via_cluster tag.
        assert ctx['request_ids_via_cluster'] == {'new-from-cluster'}

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_requests_from_managed_jobs_does_not_tag_preexisting(
            self, mock_get_tasks):
        """Symmetric: a user-seeded request that also matches the
        managed-job scan must not inherit the via_job tag."""
        body = SimpleNamespace(job_id=42,
                               job_ids=None,
                               name='task',
                               all_users=False,
                               all=False)
        mock_get_tasks.return_value = [
            _make_request(request_id='user-seeded',
                          request_body=body,
                          name='sky.jobs.launch'),
        ]
        with mock.patch('sky.utils.debug_utils.managed_jobs_core.queue_v2',
                        return_value=([], 0, {}, 0)):
            # 'user-seeded' was already in request_ids before the helper ran.
            ctx = _make_context(managed_job_ids={42},
                                request_ids={'user-seeded'})
            debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'user-seeded' in ctx['request_ids']
        # Pre-existing request was not tagged → still expandable downstream.
        assert ctx['request_ids_via_job'] == set()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    def test_all_users_cancel_does_not_drag_unrelated_jobs(
            self, mock_get_tasks, mock_get_request):
        """A historic `cancel --all-users` request must not pollute the
        dump.

        Before this fix: `_get_requests_from_managed_jobs` matches the
        request via body.all_users=True → adds it to request_ids →
        `_get_managed_jobs_from_requests` reads it back and would
        normally extract any job_ids from its body. The guard means
        none of that re-expansion happens.
        """
        cancel_body = SimpleNamespace(job_id=None,
                                      job_ids=None,
                                      name=None,
                                      all_users=True,
                                      all=False)
        # The cancel request that matched body.all_users in step 1.
        mock_get_tasks.return_value = [
            _make_request(request_id='cancel-req',
                          request_body=cancel_body,
                          name='sky.jobs.cancel'),
        ]
        with mock.patch('sky.utils.debug_utils.managed_jobs_core.queue_v2',
                        return_value=([], 0, {}, 0)):
            ctx = _make_context(managed_job_ids={1})
            debug_utils._get_requests_from_managed_jobs(ctx)

        assert 'cancel-req' in ctx['request_ids']
        assert 'cancel-req' in ctx['request_ids_via_job']

        # Step 2: the cancel request would normally be re-examined here.
        # It must be skipped because it's tagged via_job.
        debug_utils._get_managed_jobs_from_requests(ctx)

        assert ctx['managed_job_ids'] == {1}
        mock_get_request.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for create_debug_dump (end-to-end)
# ---------------------------------------------------------------------------
class TestCreateDebugDump:

    @pytest.fixture(autouse=True)
    def _mock_prefix_lookup(self):
        """Mock prefix lookup to return exact matches for test IDs."""

        def _fake_prefix_lookup(prefix, fields=None):
            return [mock.MagicMock(request_id=prefix)]

        with mock.patch(
                'sky.utils.debug_utils.requests_lib.get_requests_with_prefix',
                side_effect=_fake_prefix_lookup):
            yield

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_creates_zip_file(self, mock_req_from_clusters, mock_req_from_jobs,
                              mock_jobs_from_req, mock_clusters_from_req,
                              mock_clusters_from_jobs, mock_dump_server,
                              mock_dump_requests, mock_dump_clusters,
                              mock_dump_jobs, tmp_path):
        """create_debug_dump should produce a zip file."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(
                request_ids=['req-1'],
                cluster_names=['my-cluster'],
            )

        assert result.exists()
        assert result.suffix == '.zip'
        assert zipfile.is_zipfile(result)

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_zip_contains_summary_and_errors(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """The zip should contain summary.json and errors.json."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(request_ids=['req-1'],)

        with zipfile.ZipFile(result, 'r') as zf:
            names = zf.namelist()
            summary_files = [n for n in names if n.endswith('summary.json')]
            assert len(summary_files) == 1

            # Verify summary content
            summary_data = json.loads(zf.read(summary_files[0]))
            assert 'requested' in summary_data
            assert 'collected' in summary_data
            assert 'errors' in summary_data
            assert 'warnings' not in summary_data
            assert 'req-1' in summary_data['requested']['request_ids']

            errors_files = [n for n in names if n.endswith('errors.json')]
            assert len(errors_files) == 1

            # debug_dump.log should be present (from the file handler)
            log_files = [n for n in names if n.endswith('debug_dump.log')]
            assert len(log_files) == 1

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_includes_system_request_ids(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """System daemon request IDs should always be included."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            debug_utils.create_debug_dump()

        # Verify that _dump_request_id_info was called with system IDs
        call_args = mock_dump_requests.call_args
        request_ids_arg = call_args[0][0]
        for system_id in debug_utils.SYSTEM_REQUEST_IDS:
            assert system_id in request_ids_arg

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_includes_client_info(self, mock_req_from_clusters,
                                  mock_req_from_jobs, mock_jobs_from_req,
                                  mock_clusters_from_req,
                                  mock_clusters_from_jobs, mock_dump_server,
                                  mock_dump_requests, mock_dump_clusters,
                                  mock_dump_jobs, tmp_path):
        """Client info should be written when provided."""
        client_info = {
            'client_version': '0.10.0',
            'platform': 'linux',
        }
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(client_info=client_info)

        with zipfile.ZipFile(result, 'r') as zf:
            names = zf.namelist()
            client_info_files = [
                n for n in names if n.endswith('client_info.json')
            ]
            assert len(client_info_files) == 1
            data = json.loads(zf.read(client_info_files[0]))
            assert data['client_version'] == '0.10.0'
            assert data['platform'] == 'linux'

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_calls_populate_recent_context(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """When recent_minutes is provided, _populate_recent_context should be
        called."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')), \
             mock.patch('sky.utils.debug_utils._populate_recent_context') \
             as mock_populate:
            debug_utils.create_debug_dump(recent_minutes=120.0)

        mock_populate.assert_called_once()
        # Second argument should be the minutes
        assert mock_populate.call_args[0][1] == 120.0

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_does_not_call_populate_without_recent_minutes(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """When recent_minutes is None, _populate_recent_context should NOT
        be called."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')), \
             mock.patch('sky.utils.debug_utils._populate_recent_context') \
             as mock_populate:
            debug_utils.create_debug_dump(recent_minutes=None)

        mock_populate.assert_not_called()

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_cross_linking_called(self, mock_req_from_clusters,
                                  mock_req_from_jobs, mock_jobs_from_req,
                                  mock_clusters_from_req,
                                  mock_clusters_from_jobs, mock_dump_server,
                                  mock_dump_requests, mock_dump_clusters,
                                  mock_dump_jobs, tmp_path):
        """All cross-linking functions should be called."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            debug_utils.create_debug_dump(
                request_ids=['req-1'],
                cluster_names=['cluster-1'],
                managed_job_ids=[1],
            )

        mock_req_from_clusters.assert_called_once()
        mock_req_from_jobs.assert_called_once()
        mock_jobs_from_req.assert_called_once()
        mock_clusters_from_req.assert_called_once()
        mock_clusters_from_jobs.assert_called_once()

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_summary_contains_collected_counts(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """Summary should reflect the final collected counts."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(
                request_ids=['req-1', 'req-2'],
                cluster_names=['cluster-1'],
                managed_job_ids=[10],
            )

        with zipfile.ZipFile(result, 'r') as zf:
            names = zf.namelist()
            summary_files = [n for n in names if n.endswith('summary.json')]
            summary_data = json.loads(zf.read(summary_files[0]))

            # System request IDs are always added
            collected = summary_data['collected']
            assert collected['request_count'] >= 2  # At least our 2 + system
            assert collected['cluster_count'] >= 1
            assert collected['managed_job_count'] >= 1

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_empty_inputs_still_creates_dump(
            self, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """Calling with no inputs should still create a valid dump
        with system request IDs."""
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump()

        assert result.exists()
        assert zipfile.is_zipfile(result)

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_orphan_cleanup(self, mock_req_from_clusters, mock_req_from_jobs,
                            mock_jobs_from_req, mock_clusters_from_req,
                            mock_clusters_from_jobs, mock_dump_server,
                            mock_dump_requests, mock_dump_clusters,
                            mock_dump_jobs, tmp_path):
        """Old zip files (>1 hour) should be cleaned up."""
        dump_dir = tmp_path / 'debug_dumps'
        dump_dir.mkdir(parents=True)

        # Create an old zip file (>1 hour old)
        old_zip = dump_dir / 'debug_dump_20200101_000000.zip'
        old_zip.write_text('fake zip')
        # Set modification time to 2 hours ago
        old_mtime = time.time() - 7200
        os.utime(old_zip, (old_mtime, old_mtime))

        # Create a recent zip file (<1 hour old)
        recent_zip = dump_dir / 'debug_dump_20260217_120000.zip'
        recent_zip.write_text('recent fake zip')

        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR', str(dump_dir)):
            debug_utils.create_debug_dump()

        # Old zip should be cleaned up
        assert not old_zip.exists()
        # Recent zip should remain
        assert recent_zip.exists()

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    def test_debug_handler_cleaned_up(self, mock_req_from_clusters,
                                      mock_req_from_jobs, mock_jobs_from_req,
                                      mock_clusters_from_req,
                                      mock_clusters_from_jobs, mock_dump_server,
                                      mock_dump_requests, mock_dump_clusters,
                                      mock_dump_jobs, tmp_path):
        """The debug file handler should be removed after create_debug_dump."""
        import logging as _logging
        dbg_logger = _logging.getLogger('sky.utils.debug_utils')
        handlers_before = list(dbg_logger.handlers)

        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            debug_utils.create_debug_dump()

        # No new handlers should remain on the logger
        assert dbg_logger.handlers == handlers_before


# ---------------------------------------------------------------------------
# Tests for request ID prefix resolution in create_debug_dump
# ---------------------------------------------------------------------------
class TestRequestIdPrefixResolution:

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_requests_with_prefix')
    def test_prefix_resolves_to_all_matches(
            self, mock_get_prefix, mock_req_from_clusters, mock_req_from_jobs,
            mock_jobs_from_req, mock_clusters_from_req, mock_clusters_from_jobs,
            mock_dump_server, mock_dump_requests, mock_dump_clusters,
            mock_dump_jobs, tmp_path):
        """A prefix matching multiple requests should include all."""
        mock_get_prefix.return_value = [
            mock.MagicMock(request_id='abc-111'),
            mock.MagicMock(request_id='abc-222'),
        ]
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(request_ids=['abc'])

        with zipfile.ZipFile(result, 'r') as zf:
            summary = json.loads(
                zf.read([
                    n for n in zf.namelist() if n.endswith('summary.json')
                ][0]))
        collected_ids = summary['collected']['request_ids']
        assert 'abc-111' in collected_ids
        assert 'abc-222' in collected_ids

    @mock.patch('sky.utils.debug_utils._dump_managed_job_info')
    @mock.patch('sky.utils.debug_utils._dump_cluster_info')
    @mock.patch('sky.utils.debug_utils._dump_request_id_info')
    @mock.patch('sky.utils.debug_utils._dump_server_info')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_clusters_from_requests')
    @mock.patch('sky.utils.debug_utils._get_managed_jobs_from_requests')
    @mock.patch('sky.utils.debug_utils._get_requests_from_managed_jobs')
    @mock.patch('sky.utils.debug_utils._get_requests_from_clusters')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_requests_with_prefix')
    def test_no_match_skips_prefix(self, mock_get_prefix,
                                   mock_req_from_clusters, mock_req_from_jobs,
                                   mock_jobs_from_req, mock_clusters_from_req,
                                   mock_clusters_from_jobs, mock_dump_server,
                                   mock_dump_requests, mock_dump_clusters,
                                   mock_dump_jobs, tmp_path):
        """A prefix matching nothing should be skipped."""
        mock_get_prefix.return_value = None
        with mock.patch('sky.utils.debug_utils.DEBUG_DUMP_DIR',
                        str(tmp_path / 'debug_dumps')):
            result = debug_utils.create_debug_dump(request_ids=['nonexistent'])

        with zipfile.ZipFile(result, 'r') as zf:
            summary = json.loads(
                zf.read([
                    n for n in zf.namelist() if n.endswith('summary.json')
                ][0]))
        # The unmatched prefix should not appear in collected IDs
        # (only system request IDs should be present)
        assert 'nonexistent' not in summary['collected']['request_ids']


# ---------------------------------------------------------------------------
# Tests for DebugDumpContext structure
# ---------------------------------------------------------------------------
class TestDebugDumpContext:

    def test_context_creation(self):
        """DebugDumpContext should be creatable with expected fields."""
        ctx = _make_context(
            request_ids={'r1', 'r2'},
            cluster_names={'c1'},
            managed_job_ids={1, 2, 3},
        )
        assert ctx['request_ids'] == {'r1', 'r2'}
        assert ctx['cluster_names'] == {'c1'}
        assert ctx['managed_job_ids'] == {1, 2, 3}
        assert ctx['request_ids_via_job'] == set()
        assert ctx['request_ids_via_cluster'] == set()
        assert ctx['errors'] == []

    def test_context_sets_are_mutable(self):
        """The sets in the context should be mutable."""
        ctx = _make_context()
        ctx['request_ids'].add('new-req')
        ctx['cluster_names'].add('new-cluster')
        ctx['managed_job_ids'].add(99)
        ctx['errors'].append({'component': 'test', 'error': 'oops'})
        assert 'new-req' in ctx['request_ids']
        assert 'new-cluster' in ctx['cluster_names']
        assert 99 in ctx['managed_job_ids']
        assert len(ctx['errors']) == 1


# ---------------------------------------------------------------------------
# Tests for SYSTEM_REQUEST_IDS constant
# ---------------------------------------------------------------------------
class TestSystemRequestIds:

    def test_system_request_ids_is_list(self):
        """SYSTEM_REQUEST_IDS should be a non-empty list of strings."""
        assert isinstance(debug_utils.SYSTEM_REQUEST_IDS, list)
        assert len(debug_utils.SYSTEM_REQUEST_IDS) > 0
        for rid in debug_utils.SYSTEM_REQUEST_IDS:
            assert isinstance(rid, str)

    def test_known_system_ids(self):
        """Known system daemon IDs should be present."""
        from sky.server import constants as server_constants
        from sky.server import daemons

        # All internal daemon IDs should be present
        for daemon in daemons.INTERNAL_REQUEST_DAEMONS:
            assert daemon.id in debug_utils.SYSTEM_REQUEST_IDS
        # On-boot check should be present
        assert server_constants.ON_BOOT_CHECK_REQUEST_ID in \
            debug_utils.SYSTEM_REQUEST_IDS


# ---------------------------------------------------------------------------
# Tests for _dump_managed_job_queue_info
# ---------------------------------------------------------------------------
class TestDumpManagedJobQueueInfo:

    @mock.patch('sky.jobs.server.core.queue_v2')
    def test_writes_job_info_json(self, mock_queue_v2, tmp_path):
        """Should write job_info.json for each job."""
        mock_queue_v2.return_value = ([{
            'job_id': 1,
            'job_name': 'test-job',
            'status': 'RUNNING',
        }], 1, {}, 1)

        jobs_dir = str(tmp_path / 'managed_jobs')
        os.makedirs(jobs_dir, exist_ok=True)
        errors: List[Dict[str, str]] = []
        debug_utils._dump_managed_job_queue_info({1}, jobs_dir, errors)

        job_info_path = os.path.join(jobs_dir, '1', 'job_info.json')
        assert os.path.exists(job_info_path)
        with open(job_info_path) as f:
            data = json.load(f)
        assert data['job_id'] == 1
        assert data['job_name'] == 'test-job'
        assert not errors

    @mock.patch('sky.jobs.server.core.queue_v2')
    def test_writes_multiple_tasks(self, mock_queue_v2, tmp_path):
        """Pipeline with multiple tasks should write task-indexed files."""
        mock_queue_v2.return_value = ([
            {
                'job_id': 1,
                'task_name': 'task-a',
                'status': 'SUCCEEDED'
            },
            {
                'job_id': 1,
                'task_name': 'task-b',
                'status': 'RUNNING'
            },
        ], 1, {}, 1)

        jobs_dir = str(tmp_path / 'managed_jobs')
        os.makedirs(jobs_dir, exist_ok=True)
        debug_utils._dump_managed_job_queue_info({1}, jobs_dir, [])

        assert os.path.exists(os.path.join(jobs_dir, '1',
                                           'job_info_task0.json'))
        assert os.path.exists(os.path.join(jobs_dir, '1',
                                           'job_info_task1.json'))

    @mock.patch('sky.jobs.server.core.queue_v2')
    def test_error_handling(self, mock_queue_v2, tmp_path):
        """DB failure should record error but not crash."""
        mock_queue_v2.side_effect = RuntimeError('queue_v2 failed')

        jobs_dir = str(tmp_path / 'managed_jobs')
        os.makedirs(jobs_dir, exist_ok=True)
        errors: List[Dict[str, str]] = []
        debug_utils._dump_managed_job_queue_info({1}, jobs_dir, errors)

        assert len(errors) == 1
        assert errors[0]['component'] == 'managed_jobs'
        assert 'queue_v2 failed' in errors[0]['error']


# ---------------------------------------------------------------------------
# Tests for _collect_controller_debug_data
# ---------------------------------------------------------------------------
class TestCollectControllerDebugData:

    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_skips_when_controller_not_accessible(self, mock_accessible,
                                                  tmp_path):
        """Should skip gracefully if controller is not accessible."""
        mock_accessible.side_effect = RuntimeError('Controller not UP')

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        assert len(errors) == 1
        assert 'controller_access' in errors[0]['resource']

    @mock.patch('sky.utils.debug_utils.CloudVmRayBackend')
    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_manifest_and_rsync(self, mock_accessible, mock_backend_cls,
                                tmp_path):
        """Should write inline data and rsync file paths from manifest."""
        from sky.utils import message_utils

        mock_handle = mock.MagicMock()
        mock_runner = mock.MagicMock()
        mock_handle.get_command_runners.return_value = [mock_runner]
        mock_accessible.return_value = mock_handle

        # Simulate CodeGen manifest response
        manifest = {
            'inline_data': [{
                'relative_path': 'managed_jobs/1/job_events.json',
                'content': '{"events": []}',
            }],
            'file_paths': [{
                'remote_path': '/home/user/sky_logs/jobs_controller/1.log',
                'relative_path': 'managed_jobs/1/1.log',
            }],
            'errors': [{
                'component': 'managed_jobs',
                'resource': '1/events',
                'error': 'No events found',
            }],
        }
        encoded = message_utils.encode_payload(manifest)

        mock_backend = mock.MagicMock()
        mock_backend.run_on_head.return_value = (0, encoded, '')
        mock_backend_cls.return_value = mock_backend

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        # Inline data should be written
        events_path = os.path.join(str(tmp_path), 'managed_jobs', '1',
                                   'job_events.json')
        assert os.path.exists(events_path)
        with open(events_path, 'r', encoding='utf-8') as f:
            assert f.read() == '{"events": []}'

        # Rsync should be called for file_paths
        mock_runner.rsync.assert_called_once()
        call_kwargs = mock_runner.rsync.call_args
        assert call_kwargs[1]['source'] == (
            '/home/user/sky_logs/jobs_controller/1.log')
        assert call_kwargs[1]['up'] is False

        # Controller-side errors should be propagated
        assert len(errors) == 1
        assert errors[0]['error'] == 'No events found'

    @mock.patch('sky.utils.debug_utils.CloudVmRayBackend')
    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_rsync_file_not_found_graceful(self, mock_accessible,
                                           mock_backend_cls, tmp_path):
        """Should handle RSYNC_FILE_NOT_FOUND gracefully."""
        from sky import exceptions as sky_exceptions
        from sky.utils import message_utils

        mock_handle = mock.MagicMock()
        mock_runner = mock.MagicMock()
        mock_runner.rsync.side_effect = sky_exceptions.CommandError(
            returncode=sky_exceptions.RSYNC_FILE_NOT_FOUND_CODE,
            command='rsync ...',
            error_msg='file not found',
            detailed_reason=None)
        mock_handle.get_command_runners.return_value = [mock_runner]
        mock_accessible.return_value = mock_handle

        manifest = {
            'inline_data': [],
            'file_paths': [{
                'remote_path': '/missing/file.log',
                'relative_path': 'managed_jobs/1/1.log',
            }],
            'errors': [],
        }
        mock_backend = mock.MagicMock()
        mock_backend.run_on_head.return_value = (
            0, message_utils.encode_payload(manifest), '')
        mock_backend_cls.return_value = mock_backend

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        # Should NOT record an error for file-not-found
        assert not errors

    @mock.patch('sky.utils.debug_utils.CloudVmRayBackend')
    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_rsync_error_recorded(self, mock_accessible, mock_backend_cls,
                                  tmp_path):
        """Should record errors for non-file-not-found rsync failures."""
        from sky import exceptions as sky_exceptions
        from sky.utils import message_utils

        mock_handle = mock.MagicMock()
        mock_runner = mock.MagicMock()
        mock_runner.rsync.side_effect = sky_exceptions.CommandError(
            returncode=1,
            command='rsync ...',
            error_msg='connection refused',
            detailed_reason=None)
        mock_handle.get_command_runners.return_value = [mock_runner]
        mock_accessible.return_value = mock_handle

        manifest = {
            'inline_data': [],
            'file_paths': [{
                'remote_path': '/some/file.log',
                'relative_path': 'managed_jobs/1/1.log',
            }],
            'errors': [],
        }
        mock_backend = mock.MagicMock()
        mock_backend.run_on_head.return_value = (
            0, message_utils.encode_payload(manifest), '')
        mock_backend_cls.return_value = mock_backend

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        # Should record the rsync error
        assert len(errors) == 1
        assert 'rsync/' in errors[0]['resource']


# ---------------------------------------------------------------------------
# Tests for RequestTaskFilter.finished_after
# ---------------------------------------------------------------------------
class TestRequestTaskFilterFinishedAfter:
    """Tests for RequestTaskFilter.finished_after SQL generation."""

    def test_finished_after_generates_sql_with_null_handling(self):
        """finished_after should generate SQL including NULL (in-progress)."""
        from sky.server.requests import requests as req_lib
        f = req_lib.RequestTaskFilter(finished_after=1000.0)
        query, params = f.build_query()
        assert '(finished_at >= ? OR finished_at IS NULL)' in query
        assert 1000.0 in params

    def test_finished_after_and_before_combined(self):
        """Both finished_after and finished_before should combine."""
        from sky.server.requests import requests as req_lib
        f = req_lib.RequestTaskFilter(finished_before=2000.0,
                                      finished_after=1000.0)
        query, params = f.build_query()
        assert 'finished_at < ?' in query
        assert '(finished_at >= ? OR finished_at IS NULL)' in query
        assert 2000.0 in params
        assert 1000.0 in params


# ---------------------------------------------------------------------------
# Tests for manifest path traversal validation
# ---------------------------------------------------------------------------
class TestManifestPathTraversal:
    """Tests for manifest relative_path traversal validation."""

    @mock.patch('sky.utils.debug_utils.CloudVmRayBackend')
    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_inline_data_traversal_skipped(self, mock_accessible,
                                           mock_backend_cls, tmp_path):
        """Inline data with path traversal should be skipped."""
        from sky.utils import message_utils

        mock_handle = mock.MagicMock()
        mock_accessible.return_value = mock_handle

        manifest = {
            'inline_data': [
                {
                    'relative_path': '../../../etc/evil',
                    'content': 'malicious',
                },
                {
                    'relative_path': '/etc/passwd',
                    'content': 'malicious',
                },
                {
                    'relative_path': 'safe/path.json',
                    'content': '{"ok": true}',
                },
            ],
            'file_paths': [],
            'errors': [],
        }
        mock_backend = mock.MagicMock()
        mock_backend.run_on_head.return_value = (
            0, message_utils.encode_payload(manifest), '')
        mock_backend_cls.return_value = mock_backend

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        # Only safe path should be written
        assert os.path.exists(os.path.join(str(tmp_path), 'safe', 'path.json'))
        assert not os.path.exists(
            os.path.join(str(tmp_path), '..', '..', '..', 'etc', 'evil'))
        # Both traversal attempts should be recorded as errors
        traversal_errors = [
            e for e in errors if 'Path traversal' in e.get('error', '')
        ]
        assert len(traversal_errors) == 2

    @mock.patch('sky.utils.debug_utils.CloudVmRayBackend')
    @mock.patch('sky.utils.debug_utils.backend_utils.is_controller_accessible')
    def test_rsync_traversal_skipped(self, mock_accessible, mock_backend_cls,
                                     tmp_path):
        """Rsync entries with path traversal should be skipped."""
        from sky.utils import message_utils

        mock_handle = mock.MagicMock()
        mock_runner = mock.MagicMock()
        mock_handle.get_command_runners.return_value = [mock_runner]
        mock_accessible.return_value = mock_handle

        manifest = {
            'inline_data': [],
            'file_paths': [{
                'remote_path': '/some/file.log',
                'relative_path': '../../etc/evil.log',
            },],
            'errors': [],
        }
        mock_backend = mock.MagicMock()
        mock_backend.run_on_head.return_value = (
            0, message_utils.encode_payload(manifest), '')
        mock_backend_cls.return_value = mock_backend

        errors: List[Dict[str, str]] = []
        debug_utils._collect_controller_debug_data([1], str(tmp_path), errors)

        # rsync should NOT be called for traversal path
        mock_runner.rsync.assert_not_called()
        # Traversal should be recorded as an error
        traversal_errors = [
            e for e in errors if 'Path traversal' in e.get('error', '')
        ]
        assert len(traversal_errors) == 1


# ---------------------------------------------------------------------------
# Tests for _SENSITIVE_ENV_VARS redaction
# ---------------------------------------------------------------------------
class TestSensitiveEnvVarRedaction:
    """Tests for sensitive environment variable redaction."""

    def test_sensitive_env_vars_redacted(self):
        """Sensitive env vars should have their values replaced with bool."""
        assert 'SKYPILOT_DB_CONNECTION_URI' in debug_utils._SENSITIVE_ENV_VARS
        assert 'SKYPILOT_INITIAL_BASIC_AUTH' in debug_utils._SENSITIVE_ENV_VARS

    @mock.patch('sky.utils.debug_utils.sky_check.check', return_value={})
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request',
                return_value=None)
    def test_dump_server_info_redacts_sensitive(self, mock_req, mock_check,
                                                tmp_path):
        """_dump_server_info should redact sensitive env var values."""
        del mock_req, mock_check  # unused but required by mock.patch
        env_patch = {
            'SKYPILOT_DEBUG': '1',
            'SKYPILOT_DB_CONNECTION_URI': 'postgresql://secret@host/db',
            'SKY_NORMAL_VAR': 'visible',
        }
        with mock.patch.dict(os.environ, env_patch, clear=False):
            debug_utils._dump_server_info(str(tmp_path))

        with open(os.path.join(str(tmp_path), 'server_info.json')) as f:
            info = json.load(f)

        env = info['environment']
        assert env['SKYPILOT_DEBUG'] == '1'
        assert env['SKY_NORMAL_VAR'] == 'visible'
        # Sensitive var should be redacted to bool
        assert env['SKYPILOT_DB_CONNECTION_URI'] is True


# ---------------------------------------------------------------------------
# Tests for serialize_cluster_record
# ---------------------------------------------------------------------------
class TestSerializeClusterRecord:

    def _make_mock_handle(self, **overrides):
        """Create a mock handle with all expected attributes."""
        defaults = {
            'cluster_name': 'test-cluster',
            'cluster_name_on_cloud': 'sky-abc-test-cluster',
            'head_ip': '10.0.0.1',
            'launched_nodes': 2,
            'launched_resources': 'AWS(p3.2xlarge)',
            'stable_internal_external_ips': [('10.0.0.1', '54.1.2.3'),
                                             ('10.0.0.2', '54.1.2.4')],
            'stable_ssh_ports': [22, 22],
            'docker_user': 'sky_user',
            'ssh_user': 'ubuntu',
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_full_cluster_record(self, **overrides):
        """Create a full cluster record dict matching get_cluster_from_name."""
        defaults = {
            'name': 'test-cluster',
            'cluster_hash': 'abc123',
            'status': 'UP',
            'launched_at': 1700000000.0,
            'autostop': 30,
            'to_down': False,
            'cluster_ever_up': True,
            'status_updated_at': 1700001000.0,
            'config_hash': 'cfg-hash',
            'workspace': 'default',
            'is_managed': False,
            'user_hash': 'user-abc',
            'user_name': 'testuser',
            'last_use': 'sky exec test-cluster -- echo hi',
            'owner': ['testuser'],
            'metadata': {
                'sky_version': '0.10.0'
            },
            'last_creation_command': 'sky launch test.yaml',
            'last_creation_yaml': 'resources:\\n  cloud: aws',
            'last_event': 'cluster UP',
            'handle': self._make_mock_handle(),
        }
        defaults.update(overrides)
        return defaults

    def test_all_expected_keys_present(self):
        """All expected keys should be present in serialized output."""
        record = self._make_full_cluster_record()
        result = debug_dump_helpers.serialize_cluster_record(record)

        expected_top_keys = {
            'name', 'cluster_hash', 'status', 'launched_at',
            'launched_at_human', 'autostop', 'to_down', 'cluster_ever_up',
            'status_updated_at', 'status_updated_at_human', 'config_hash',
            'workspace', 'is_managed', 'user_hash', 'user_name', 'last_use',
            'owner', 'metadata', 'last_creation_command', 'last_creation_yaml',
            'last_event', 'handle'
        }
        assert set(result.keys()) == expected_top_keys

        expected_handle_keys = {
            'cluster_name', 'cluster_name_on_cloud', 'head_ip',
            'launched_nodes', 'launched_resources',
            'stable_internal_external_ips', 'stable_ssh_ports', 'docker_user',
            'ssh_user'
        }
        assert set(result['handle'].keys()) == expected_handle_keys

    def test_minimal_record(self):
        """A near-empty record with no handle should not crash."""
        record = {'name': 'bare-cluster'}
        result = debug_dump_helpers.serialize_cluster_record(record)

        assert result['name'] == 'bare-cluster'
        assert result['handle'] == {}
        # Missing fields default to None
        assert result['cluster_hash'] is None
        assert result['launched_at'] is None
        assert result['last_use'] is None
        assert result['owner'] is None
        assert result['metadata'] is None
        assert result['last_creation_command'] is None
        assert result['last_creation_yaml'] is None
        assert result['last_event'] is None

    def test_handle_fields_extracted(self):
        """Handle sub-fields should be correctly extracted."""
        handle = self._make_mock_handle(
            stable_internal_external_ips=[('10.0.0.1', '1.2.3.4')],
            stable_ssh_ports=[2222],
            docker_user='docker_u',
            ssh_user='ec2-user',
        )
        record = {'name': 'c', 'handle': handle}
        result = debug_dump_helpers.serialize_cluster_record(record)

        h = result['handle']
        assert h['stable_internal_external_ips'] == [('10.0.0.1', '1.2.3.4')]
        assert h['stable_ssh_ports'] == [2222]
        assert h['docker_user'] == 'docker_u'
        assert h['ssh_user'] == 'ec2-user'

    def test_timestamps_have_human_readable(self):
        """Epoch timestamps should produce valid ISO datetime strings."""
        record = self._make_full_cluster_record(
            launched_at=1700000000.0,
            status_updated_at=1700001000.0,
        )
        result = debug_dump_helpers.serialize_cluster_record(record)

        # Both human-readable fields should be valid ISO datetimes
        launched_human = result['launched_at_human']
        assert launched_human is not None
        dt = datetime.datetime.fromisoformat(launched_human)
        assert dt.year == 2023

        updated_human = result['status_updated_at_human']
        assert updated_human is not None
        datetime.datetime.fromisoformat(updated_human)

    def test_last_creation_yaml_redacted(self):
        """Secrets in last_creation_yaml should be redacted."""
        yaml_str = ('name: my-task\n'
                    'secrets:\n'
                    '  API_KEY: super_secret_key\n'
                    'resources:\n'
                    '  cloud: aws\n')
        record = self._make_full_cluster_record(last_creation_yaml=yaml_str)
        result = debug_dump_helpers.serialize_cluster_record(record)
        assert 'super_secret_key' not in result['last_creation_yaml']
        assert '<redacted>' in result['last_creation_yaml']
        # Non-secret fields should still be present
        assert 'my-task' in result['last_creation_yaml']

    def test_last_creation_yaml_none_stays_none(self):
        """None last_creation_yaml should remain None."""
        record = self._make_full_cluster_record(last_creation_yaml=None)
        result = debug_dump_helpers.serialize_cluster_record(record)
        assert result['last_creation_yaml'] is None


# ---------------------------------------------------------------------------
# Tests for redact_config
# ---------------------------------------------------------------------------
class TestRedactConfig:

    def test_redacts_sensitive_fields(self):
        """Sensitive fields should be replaced with '<redacted>'."""
        config = {
            'api_server': {
                'endpoint': 'https://my-server.example.com',
                'service_account_token': 'sky_secret123',
                'requests_retention_hours': 24,
            },
            'jobs': {
                'controller': {
                    'consolidation_mode': True
                }
            },
        }
        result = debug_dump_helpers.redact_config(config)
        assert result['api_server']['endpoint'] == '<redacted>'
        assert result['api_server']['service_account_token'] == '<redacted>'
        # Non-sensitive fields preserved
        assert result['api_server']['requests_retention_hours'] == 24
        assert result['jobs']['controller']['consolidation_mode'] is True

    def test_no_sensitive_fields(self):
        """Config without sensitive keys should pass through unchanged."""
        config = {
            'jobs': {
                'controller': {
                    'consolidation_mode': False
                }
            },
            'kubernetes': {
                'networking': 'nodeport'
            },
        }
        result = debug_dump_helpers.redact_config(config)
        assert result == config

    def test_empty_config(self):
        """Empty config should not crash."""
        result = debug_dump_helpers.redact_config({})
        assert not result

    def test_does_not_mutate_original(self):
        """Original config dict should not be modified."""
        config = {
            'api_server': {
                'service_account_token': 'sky_secret',
            },
        }
        debug_dump_helpers.redact_config(config)
        assert config['api_server']['service_account_token'] == 'sky_secret'


# ---------------------------------------------------------------------------
# Tests for _REQUEST_BODY_ALLOWLIST coverage
# ---------------------------------------------------------------------------
class TestRequestBodyAllowlistCoverage:
    """Every RequestName must be in the allowlist or the test denylist."""

    # Request names intentionally excluded from the allowlist because they
    # contain sensitive non-task data (passwords, config dicts, recipe YAML).
    # Update this set when adding new request names that should be excluded.
    # Unless the request has sensitive values, it should be added to
    # _REQUEST_BODY_ALLOWLIST in sky/utils/debug_utils.py instead.
    _EXCLUDED_REQUEST_NAMES = {
        # Workspace config: config dict may contain credentials
        'sky.workspaces.update',
        'sky.workspaces.create',
        'sky.workspaces.update_config',
        # Recipe content: YAML content similar to tasks
        'sky.recipes.create',
        'sky.recipes.update',
    }

    def test_all_request_names_covered(self):
        """Every RequestName must be in the allowlist or the test denylist."""
        all_request_names = {
            server_constants.REQUEST_NAME_PREFIX + r.value
            for r in request_names.RequestName
        }
        covered = (set(debug_utils._REQUEST_BODY_ALLOWLIST.keys()) |
                   self._EXCLUDED_REQUEST_NAMES)
        uncovered = all_request_names - covered
        assert not uncovered, (
            f'Request names not in allowlist or denylist: {uncovered}. '
            'Add to _REQUEST_BODY_ALLOWLIST in debug_utils.py or '
            '_EXCLUDED_REQUEST_NAMES in this test.')

    def test_no_stale_allowlist_entries(self):
        """Allowlist should not contain entries not in RequestName enum."""
        all_request_names = {
            server_constants.REQUEST_NAME_PREFIX + r.value
            for r in request_names.RequestName
        }
        for name in debug_utils._REQUEST_BODY_ALLOWLIST:
            assert name in all_request_names, (
                f'Stale allowlist entry: {name} is not a valid RequestName')

    def test_no_stale_denylist_entries(self):
        """Denylist should not contain entries not in RequestName enum."""
        all_request_names = {
            server_constants.REQUEST_NAME_PREFIX + r.value
            for r in request_names.RequestName
        }
        for name in self._EXCLUDED_REQUEST_NAMES:
            assert name in all_request_names, (
                f'Stale denylist entry: {name} is not a valid RequestName')


# ---------------------------------------------------------------------------
# Tests for _sanitize_request_body
# ---------------------------------------------------------------------------
class TestSanitizeRequestBody:

    def test_excluded_request_returns_none(self):
        """Requests not in allowlist should return None."""
        request = _make_request(name='sky.workspaces.update')
        result = debug_utils._sanitize_request_body(request)
        assert result is None

    def test_allowed_request_with_none_body(self):
        """Allowed request with None body should return None."""
        request = _make_request(name='sky.status', request_body=None)
        result = debug_utils._sanitize_request_body(request)
        assert result is None

    def test_allowed_verbatim_request(self):
        """Allowed verbatim request should return model_dump()."""

        class FakeBody:

            def model_dump(self):
                return {'cluster_name': 'test', 'refresh': True}

        request = _make_request(name='sky.status', request_body=FakeBody())
        result = debug_utils._sanitize_request_body(request)
        assert result == {'cluster_name': 'test', 'refresh': True}

    def test_sensitive_env_vars_redacted(self):
        """Sensitive env vars in request body should be redacted."""

        class FakeBody:

            def model_dump(self):
                return {
                    'cluster_name': 'test',
                    'env_vars': {
                        'NORMAL_VAR': 'visible',
                        'AWS_SECRET_ACCESS_KEY': 'super-secret',
                        'SKYPILOT_DB_CONNECTION_URI': 'postgres://...',
                    }
                }

        request = _make_request(name='sky.stop', request_body=FakeBody())
        result = debug_utils._sanitize_request_body(request)
        assert result is not None
        assert result['env_vars']['NORMAL_VAR'] == 'visible'
        assert result['env_vars']['AWS_SECRET_ACCESS_KEY'] == '<redacted>'
        assert result['env_vars']['SKYPILOT_DB_CONNECTION_URI'] == '<redacted>'

    def test_task_yaml_field_redacted(self):
        """Task YAML fields should have secrets redacted."""
        task_yaml = ('name: my-task\n'
                     'secrets:\n'
                     '  MY_SECRET: secret_value\n'
                     'resources:\n'
                     '  cloud: aws\n'
                     '  _docker_login_config:\n'
                     '    password: docker_pass\n')

        class FakeBody:

            def model_dump(self):
                return {'task': task_yaml, 'cluster_name': 'test'}

        request = _make_request(name='sky.launch', request_body=FakeBody())
        result = debug_utils._sanitize_request_body(request)
        assert result is not None
        # The redacted YAML should not contain the actual secret values
        assert 'secret_value' not in result['task']
        assert 'docker_pass' not in result['task']
        assert '<redacted>' in result['task']
        # Cluster name should be unchanged
        assert result['cluster_name'] == 'test'

    def test_model_dump_failure_returns_none(self):
        """If model_dump() raises, should return None."""

        class BadBody:

            def model_dump(self):
                raise RuntimeError('oops')

        request = _make_request(name='sky.status', request_body=BadBody())
        result = debug_utils._sanitize_request_body(request)
        assert result is None


# ---------------------------------------------------------------------------
# Tests for redact_task_yaml
# ---------------------------------------------------------------------------
class TestRedactTaskYaml:

    def test_redacts_secrets(self):
        """Secrets should be redacted."""
        yaml_str = ('name: my-task\n'
                    'secrets:\n'
                    '  API_KEY: real_api_key\n'
                    '  TOKEN: real_token\n')
        result = debug_dump_helpers.redact_task_yaml(yaml_str)
        assert 'real_api_key' not in result
        assert 'real_token' not in result
        assert '<redacted>' in result

    def test_redacts_docker_password(self):
        """Docker login password should be redacted."""
        yaml_str = ('name: my-task\n'
                    'resources:\n'
                    '  _docker_login_config:\n'
                    '    password: my_docker_pass\n')
        result = debug_dump_helpers.redact_task_yaml(yaml_str)
        assert 'my_docker_pass' not in result
        assert '<redacted>' in result

    def test_invalid_yaml_returns_error_string(self):
        """Invalid YAML should return a redacted error string."""
        result = debug_dump_helpers.redact_task_yaml(': invalid: yaml: {{')
        assert result == '<parse error, redacted>'

    def test_multi_doc_yaml(self):
        """Multi-document YAML (dag) should redact each document."""
        yaml_str = ('name: task1\n'
                    'secrets:\n'
                    '  KEY1: val1\n'
                    '---\n'
                    'name: task2\n'
                    'secrets:\n'
                    '  KEY2: val2\n')
        result = debug_dump_helpers.redact_task_yaml(yaml_str)
        assert 'val1' not in result
        assert 'val2' not in result


# ---------------------------------------------------------------------------
# Tests for _dump_request_id_info
# ---------------------------------------------------------------------------
class TestDumpRequestIdInfo:
    """Tests for the _dump_request_id_info function."""

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_happy_path_writes_request_info(self, mock_get_request, tmp_path):
        """Should write request_info.json with correct fields."""
        mock_get_request.return_value = _make_request(
            request_id='req-1',
            name='sky.launch',
            status='SUCCEEDED',
            cluster_name='my-cluster',
            created_at=1700000000.0,
            finished_at=1700001000.0,
        )

        errors: List[Dict[str, str]] = []
        debug_utils._dump_request_id_info({'req-1'}, str(tmp_path), errors)

        info_path = tmp_path / 'requests' / 'req-1' / 'request_info.json'
        assert info_path.exists()
        with open(info_path) as f:
            data = json.load(f)
        assert data['request_id'] == 'req-1'
        assert data['name'] == 'sky.launch'
        assert data['status'] == 'SUCCEEDED'
        assert data['cluster_name'] == 'my-cluster'
        assert not errors

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_request_not_found(self, mock_get_request, tmp_path):
        """Should handle request not found gracefully."""
        mock_get_request.return_value = None

        errors: List[Dict[str, str]] = []
        debug_utils._dump_request_id_info({'req-missing'}, str(tmp_path),
                                          errors)

        # No crash, no error recorded (not-found is not an error)
        assert not errors
        info_path = tmp_path / 'requests' / 'req-missing' / 'request_info.json'
        assert not info_path.exists()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_db_failure_records_error(self, mock_get_request, tmp_path):
        """DB failure should record error but not crash."""
        mock_get_request.side_effect = RuntimeError('DB is down')

        errors: List[Dict[str, str]] = []
        debug_utils._dump_request_id_info({'req-fail'}, str(tmp_path), errors)

        assert len(errors) == 1
        assert errors[0]['component'] == 'requests'
        assert 'DB is down' in errors[0]['error']
        assert 'traceback' in errors[0]

    def test_empty_request_ids_is_noop(self, tmp_path):
        """Empty request_ids should not create any files."""
        errors: List[Dict[str, str]] = []
        debug_utils._dump_request_id_info(set(), str(tmp_path), errors)

        assert not errors
        assert not (tmp_path / 'requests').exists()

    @mock.patch('sky.utils.debug_utils.shutil.copy2')
    @mock.patch('sky.utils.debug_utils.requests_lib.get_request')
    def test_copies_log_file_when_exists(self, mock_get_request, mock_copy2,
                                         tmp_path):
        """Should copy request log when it exists."""
        mock_get_request.return_value = _make_request(request_id='req-log')

        with mock.patch('pathlib.Path.exists', return_value=True):
            errors: List[Dict[str, str]] = []
            debug_utils._dump_request_id_info({'req-log'}, str(tmp_path),
                                              errors)

        # copy2 should be called at least once (for the log file)
        assert mock_copy2.called


# ---------------------------------------------------------------------------
# Tests for _dump_cluster_info
# ---------------------------------------------------------------------------
class TestDumpClusterInfo:
    """Tests for the _dump_cluster_info function."""

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_happy_path_writes_cluster_info(self, mock_get_cluster, mock_events,
                                            mock_requests, tmp_path):
        """Should write cluster_info.json for found clusters."""
        del mock_events, mock_requests  # unused but required by mock.patch
        mock_get_cluster.return_value = {
            'name': 'my-cluster',
            'cluster_hash': 'abc123',
            'status': 'UP',
            'launched_at': 1700000000.0,
            'status_updated_at': 1700001000.0,
        }

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'my-cluster'}, str(tmp_path), errors)

        info_path = (tmp_path / 'clusters' / 'my-cluster' / 'cluster_info.json')
        assert info_path.exists()
        with open(info_path) as f:
            data = json.load(f)
        assert data['name'] == 'my-cluster'
        assert data['cluster_hash'] == 'abc123'
        assert not errors

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_cluster_not_found(self, mock_get_cluster, mock_requests, tmp_path):
        """Should handle missing cluster gracefully."""
        del mock_requests  # unused but required by mock.patch
        mock_get_cluster.return_value = None

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'gone-cluster'}, str(tmp_path), errors)

        assert not errors
        info_path = (tmp_path / 'clusters' / 'gone-cluster' /
                     'cluster_info.json')
        assert not info_path.exists()

    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_db_failure_records_error(self, mock_get_cluster, tmp_path):
        """DB failure should record error but not crash."""
        mock_get_cluster.side_effect = RuntimeError('DB error')

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'fail-cluster'}, str(tmp_path), errors)

        assert len(errors) >= 1
        assert any(e['component'] == 'clusters' for e in errors)

    def test_empty_cluster_names_is_noop(self, tmp_path):
        """Empty cluster_names should not create any files."""
        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info(set(), str(tmp_path), errors)

        assert not errors
        assert not (tmp_path / 'clusters').exists()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data')
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_writes_event_files(self, mock_get_cluster, mock_events,
                                mock_requests, tmp_path):
        """Should write event files for cluster events."""
        del mock_requests  # unused but required by mock.patch
        mock_get_cluster.return_value = {
            'name': 'ev-cluster',
            'cluster_hash': 'hash123',
            'status': 'UP',
        }
        mock_events.return_value = [{
            'event_type': 'provision',
            'events': [{
                'timestamp': '2023-01-01',
                'message': 'provisioned'
            }],
        }]

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'ev-cluster'}, str(tmp_path), errors)

        event_path = (tmp_path / 'clusters' / 'ev-cluster' /
                      'events_provision.json')
        assert event_path.exists()

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks')
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_writes_associated_requests(self, mock_get_cluster, mock_events,
                                        mock_get_tasks, tmp_path):
        """Should write associated_requests.json."""
        del mock_events  # unused but required by mock.patch
        mock_get_cluster.return_value = {
            'name': 'assoc-cluster',
            'cluster_hash': 'hash456',
            'status': 'UP',
        }
        mock_get_tasks.return_value = [
            _make_request(request_id='req-a', name='sky.launch'),
        ]

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'assoc-cluster'}, str(tmp_path), errors)

        assoc_path = (tmp_path / 'clusters' / 'assoc-cluster' /
                      'associated_requests.json')
        assert assoc_path.exists()
        with open(assoc_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]['request_id'] == 'req-a'

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_up_cluster_collects_skylet_log(self, mock_get_cluster, mock_events,
                                            mock_requests, tmp_path):
        """An UP cluster with a handle should rsync the skylet log."""
        del mock_events, mock_requests  # unused but required by mock.patch
        handle = mock.Mock()
        runner = mock.Mock()
        # Head node resolves the skylet log path against $HOME.
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        handle.get_command_runners.return_value = [runner]
        mock_get_cluster.return_value = {
            'name': 'live-cluster',
            'cluster_hash': 'abc',
            'status': status_lib.ClusterStatus.UP,
            'handle': handle,
        }

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'live-cluster'}, str(tmp_path), errors)

        # Pulled the skylet log off the head node (runners[0]) into the
        # cluster dump dir, using the remotely-resolved source path.
        runner.rsync.assert_called_once()
        _, kwargs = runner.rsync.call_args
        assert kwargs['source'] == '/home/sky/.sky/skylet.log'
        assert kwargs['target'] == str(tmp_path / 'clusters' / 'live-cluster' /
                                       'skylet.log')
        assert kwargs['up'] is False
        assert not errors

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_stopped_cluster_skips_skylet_log(self, mock_get_cluster,
                                              mock_events, mock_requests,
                                              tmp_path):
        """A STOPPED cluster has no reachable node, so we don't attempt it."""
        del mock_events, mock_requests  # unused but required by mock.patch
        handle = mock.Mock()
        mock_get_cluster.return_value = {
            'name': 'stopped-cluster',
            'cluster_hash': 'abc',
            'status': status_lib.ClusterStatus.STOPPED,
            'handle': handle,
        }

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'stopped-cluster'}, str(tmp_path),
                                       errors)

        handle.get_command_runners.assert_not_called()
        assert not errors

    @mock.patch('sky.utils.debug_utils.requests_lib.get_request_tasks',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.debug_dump_helpers'
                '.get_cluster_events_data',
                return_value=[])
    @mock.patch('sky.utils.debug_utils.global_user_state'
                '.get_cluster_from_name')
    def test_init_cluster_collects_skylet_log(self, mock_get_cluster,
                                              mock_events, mock_requests,
                                              tmp_path):
        """An INIT (possibly degraded) cluster is still attempted."""
        del mock_events, mock_requests  # unused but required by mock.patch
        handle = mock.Mock()
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        handle.get_command_runners.return_value = [runner]
        mock_get_cluster.return_value = {
            'name': 'init-cluster',
            'cluster_hash': 'abc',
            'status': status_lib.ClusterStatus.INIT,
            'handle': handle,
        }

        errors: List[Dict[str, str]] = []
        debug_utils._dump_cluster_info({'init-cluster'}, str(tmp_path), errors)

        runner.rsync.assert_called_once()
        assert not errors


class TestCollectClusterSkyletLog:
    """Tests for the _collect_cluster_skylet_log helper."""

    def test_rsyncs_skylet_log_from_head(self, tmp_path):
        """Pulls the remotely-resolved skylet log off the first (head) runner."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner, mock.Mock()]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        # Only the head runner (index 0) is used.
        runner.rsync.assert_called_once()
        handle.get_command_runners.return_value[1].rsync.assert_not_called()
        _, kwargs = runner.rsync.call_args
        assert kwargs['source'] == '/home/sky/.sky/skylet.log'
        assert kwargs['target'] == os.path.join(str(tmp_path), 'skylet.log')
        # Bounded by a total timeout so one stalled node can't hang the dump.
        assert kwargs['timeout'] == debug_utils._SKYLET_LOG_RSYNC_TIMEOUT
        assert not errors

    def test_uses_relocated_runtime_dir(self, tmp_path):
        """A relocated SKY_RUNTIME_DIR (Slurm/devspaces) is honored because the
        path is resolved on the remote node, not from a Python attribute."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/scratch/rt/.sky/skylet.log\n', '')
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        _, kwargs = runner.rsync.call_args
        assert kwargs['source'] == '/scratch/rt/.sky/skylet.log'
        assert not errors

    def test_missing_log_is_not_an_error(self, tmp_path):
        """A not-found rsync (code 23) is silently skipped, not recorded."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        runner.rsync.side_effect = exceptions.CommandError(
            exceptions.RSYNC_FILE_NOT_FOUND_CODE, 'rsync', 'not found', None)
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        assert not errors

    def test_other_rsync_failure_is_recorded(self, tmp_path):
        """A non-23 rsync failure is recorded in errors."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        runner.rsync.side_effect = exceptions.CommandError(
            255, 'rsync', 'connection refused', None)
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        assert len(errors) == 1
        assert errors[0]['component'] == 'clusters'
        assert errors[0]['resource'] == 'c/skylet_log'

    def test_empty_runners_is_noop(self, tmp_path):
        """No runners (e.g. headless cluster) is a safe no-op."""
        handle = mock.Mock()
        handle.get_command_runners.return_value = []

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        assert not errors

    def test_get_command_runners_failure_is_recorded(self, tmp_path):
        """A failure obtaining runners is recorded but does not raise."""
        handle = mock.Mock()
        handle.get_command_runners.side_effect = RuntimeError('unreachable')

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log('c', str(tmp_path), handle,
                                                errors)

        assert len(errors) == 1
        assert errors[0]['resource'] == 'c/skylet_log'

    def test_init_rsync_failure_is_demoted_not_recorded(self, tmp_path):
        """For an INIT cluster, a (non-23) rsync failure is expected and is
        debug-logged, not recorded as a dump error."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        runner.rsync.side_effect = exceptions.CommandError(
            255, 'rsync', 'connection refused', None)
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log(
            'c',
            str(tmp_path),
            handle,
            errors,
            status=status_lib.ClusterStatus.INIT)

        assert not errors

    def test_init_get_runners_failure_is_demoted_not_recorded(self, tmp_path):
        """For an INIT cluster, an unreachable node (no runners yet) is
        expected and not recorded."""
        handle = mock.Mock()
        handle.get_command_runners.side_effect = RuntimeError('not up yet')

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log(
            'c',
            str(tmp_path),
            handle,
            errors,
            status=status_lib.ClusterStatus.INIT)

        assert not errors

    def test_up_rsync_failure_is_recorded(self, tmp_path):
        """For an UP cluster, a (non-23) rsync failure is genuinely worth
        surfacing and is recorded."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/home/sky/.sky/skylet.log\n', '')
        runner.rsync.side_effect = exceptions.CommandError(
            255, 'rsync', 'connection refused', None)
        handle = mock.Mock()
        handle.get_command_runners.return_value = [runner]

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log(
            'c',
            str(tmp_path),
            handle,
            errors,
            status=status_lib.ClusterStatus.UP)

        assert len(errors) == 1
        assert errors[0]['resource'] == 'c/skylet_log'

    def test_empty_exception_message_falls_back_to_type_name(self, tmp_path):
        """A recorded error never has a blank message: an exception whose
        str() is empty falls back to the exception type name."""
        handle = mock.Mock()
        handle.get_command_runners.side_effect = RuntimeError('')

        errors: List[Dict[str, str]] = []
        debug_utils._collect_cluster_skylet_log(
            'c',
            str(tmp_path),
            handle,
            errors,
            status=status_lib.ClusterStatus.UP)

        assert len(errors) == 1
        assert errors[0]['error'] == 'RuntimeError'


class TestResolveRemoteSkyletLogPath:
    """Tests for the _resolve_remote_skylet_log_path helper."""

    def test_returns_resolved_remote_path(self):
        """Returns the path echoed by the remote shell."""
        runner = mock.Mock()
        runner.run.return_value = (0, '/scratch/rt/.sky/skylet.log\n', '')

        path = debug_utils._resolve_remote_skylet_log_path(runner, 'c')

        assert path == '/scratch/rt/.sky/skylet.log'
        _, kwargs = runner.run.call_args
        # Resolved with source_bashrc to match how skylet is started.
        assert kwargs['source_bashrc'] is True
        assert kwargs['require_outputs'] is True
        # Bounded by a connect timeout so an unreachable node fails fast.
        assert kwargs['connect_timeout'] == (
            debug_utils._SKYLET_LOG_RESOLVE_CONNECT_TIMEOUT)

    def test_takes_last_non_empty_line(self):
        """bashrc banner/warning lines before the echo are ignored."""
        runner = mock.Mock()
        runner.run.return_value = (0,
                                   'motd banner\n\n/home/sky/.sky/skylet.log\n',
                                   '')

        path = debug_utils._resolve_remote_skylet_log_path(runner, 'c')

        assert path == '/home/sky/.sky/skylet.log'

    def test_falls_back_on_nonzero_returncode(self):
        """A failed resolution command falls back to ~/.sky/skylet.log."""
        runner = mock.Mock()
        runner.run.return_value = (1, '', 'boom')

        path = debug_utils._resolve_remote_skylet_log_path(runner, 'c')

        assert path == posixpath.join('~', skylet_constants.SKYLET_LOG_FILE)

    def test_falls_back_on_empty_output(self):
        """Empty stdout falls back to ~/.sky/skylet.log."""
        runner = mock.Mock()
        runner.run.return_value = (0, '   \n', '')

        path = debug_utils._resolve_remote_skylet_log_path(runner, 'c')

        assert path == posixpath.join('~', skylet_constants.SKYLET_LOG_FILE)

    def test_falls_back_on_exception(self):
        """A raising runner.run falls back instead of propagating."""
        runner = mock.Mock()
        runner.run.side_effect = RuntimeError('unreachable')

        path = debug_utils._resolve_remote_skylet_log_path(runner, 'c')

        assert path == posixpath.join('~', skylet_constants.SKYLET_LOG_FILE)


class TestCollectClusterKubernetesResources:
    """Tests for the _collect_cluster_kubernetes_resources helper."""

    def _k8s_handle(self, runners):
        handle = mock.Mock()
        handle.launched_resources.cloud = clouds.Kubernetes()
        handle.cluster_name_on_cloud = 'cluster-abc'
        handle.get_command_runners.return_value = runners
        return handle

    def _k8s_runner(self, context, namespace, pod_name):
        runner = mock.MagicMock(spec=command_runner.KubernetesCommandRunner)
        runner.context = context
        runner.namespace = namespace
        runner.pod_name = pod_name
        return runner

    def test_non_kubernetes_cluster_is_noop(self, tmp_path):
        """A cluster on another cloud must not touch the k8s code path."""
        handle = mock.Mock()
        handle.launched_resources.cloud = mock.Mock()  # not a Kubernetes cloud

        errors: List[Dict[str, str]] = []
        with mock.patch('sky.provision.kubernetes.debug.dump_cluster_resources'
                       ) as dump:
            debug_utils._collect_cluster_kubernetes_resources(
                'c', str(tmp_path), handle, errors)

        dump.assert_not_called()
        handle.get_command_runners.assert_not_called()
        assert not errors

    def test_delegates_with_coordinates_from_runners(self, tmp_path):
        """Context/namespace are pulled off the k8s runners and passed through;
        the dump finds the cluster's objects by label, so no pod names needed."""
        runners = [
            self._k8s_runner('ctx', 'ns', 'pod-head'),
            self._k8s_runner('ctx', 'ns', 'pod-worker'),
        ]
        handle = self._k8s_handle(runners)

        errors: List[Dict[str, str]] = []
        with mock.patch('sky.provision.kubernetes.debug.dump_cluster_resources',
                        return_value=[]) as dump:
            debug_utils._collect_cluster_kubernetes_resources(
                'mycluster', str(tmp_path), handle, errors)

        dump.assert_called_once_with(context='ctx',
                                     namespace='ns',
                                     cluster_name_on_cloud='cluster-abc',
                                     output_dir=os.path.join(
                                         str(tmp_path), 'kubernetes'))
        assert not errors
        # A context.json mapping is dropped pointing at the per-context dump.
        with open(os.path.join(str(tmp_path), 'kubernetes', 'context.json'),
                  encoding='utf-8') as f:
            mapping = json.load(f)
        assert mapping['context'] == 'ctx'
        assert mapping['namespace'] == 'ns'
        assert mapping['cluster_name_on_cloud'] == 'cluster-abc'
        assert mapping['context_dir'].startswith('kubernetes_contexts/')

    def test_provider_errors_are_prefixed_with_cluster(self, tmp_path):
        """Errors from the provider are re-tagged with component + cluster."""
        handle = self._k8s_handle([self._k8s_runner('ctx', 'ns', 'pod-head')])
        provider_errors = [{
            'resource': 'kubernetes/pods/pod-head',
            'error': 'boom',
            'traceback': 'tb',
        }]

        errors: List[Dict[str, str]] = []
        with mock.patch('sky.provision.kubernetes.debug.dump_cluster_resources',
                        return_value=provider_errors):
            debug_utils._collect_cluster_kubernetes_resources(
                'mycluster', str(tmp_path), handle, errors)

        assert errors == [{
            'component': 'clusters',
            'resource': 'mycluster/kubernetes/pods/pod-head',
            'error': 'boom',
            'traceback': 'tb',
        }]

    def test_get_command_runners_failure_is_recorded(self, tmp_path):
        """A k8s cluster whose runners can't be built records one error."""
        handle = self._k8s_handle([])
        handle.get_command_runners.side_effect = RuntimeError('unreachable')

        errors: List[Dict[str, str]] = []
        with mock.patch('sky.provision.kubernetes.debug.dump_cluster_resources'
                       ) as dump:
            debug_utils._collect_cluster_kubernetes_resources(
                'mycluster', str(tmp_path), handle, errors)

        dump.assert_not_called()
        assert len(errors) == 1
        assert errors[0]['resource'] == 'mycluster/kubernetes'
        assert errors[0]['component'] == 'clusters'

    def test_no_kubernetes_runners_is_noop(self, tmp_path):
        """A k8s-cloud handle whose runners aren't KubernetesCommandRunners
        (e.g. cluster info unavailable) is skipped without error."""
        handle = self._k8s_handle([mock.Mock()])  # plain runner, wrong type

        errors: List[Dict[str, str]] = []
        with mock.patch('sky.provision.kubernetes.debug.dump_cluster_resources'
                       ) as dump:
            debug_utils._collect_cluster_kubernetes_resources(
                'mycluster', str(tmp_path), handle, errors)

        dump.assert_not_called()
        assert not errors


class TestSanitizeContextName:
    """Tests for _sanitize_context_name (context -> safe dir name)."""

    def test_none_maps_to_in_cluster(self):
        assert debug_utils._sanitize_context_name(None) == 'in-cluster'

    def test_unsafe_chars_replaced_and_hash_suffixed(self):
        out = debug_utils._sanitize_context_name(
            'gke_proj_us-central1-c_my-cluster')
        # Path-safe, and a short hash distinguishes contexts that would
        # otherwise collide after sanitization.
        assert '/' not in out
        assert out.startswith('gke_proj_us-central1-c_my-cluster-')

    def test_distinct_contexts_get_distinct_dirs(self):
        # Two contexts that sanitize to the same prefix still differ via hash.
        a = debug_utils._sanitize_context_name('a/b')
        b = debug_utils._sanitize_context_name('a:b')
        assert a != b

    def test_stable_for_same_input(self):
        assert (debug_utils._sanitize_context_name('ctx-x') ==
                debug_utils._sanitize_context_name('ctx-x'))


class TestDumpKubeContextsInfo:
    """Tests for _dump_kube_contexts_info.

    Source of truth is Kubernetes.existing_allowed_contexts() (not the dumped
    clusters), so these patch that classmethod.
    """

    @staticmethod
    def _patch_allowed(contexts):
        return mock.patch.object(debug_utils.clouds.Kubernetes,
                                 'existing_allowed_contexts',
                                 return_value=contexts)

    def test_dumps_each_allowed_context(self, tmp_path):
        errors: List[Dict[str, str]] = []
        with self._patch_allowed(['ctx-a', 'ctx-b']), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        return_value=[]) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        fetched = {c.kwargs['context'] for c in dump.call_args_list}
        assert fetched == {'ctx-a', 'ctx-b'}
        assert not errors

    def test_allowed_context_with_no_clusters_is_still_dumped(self, tmp_path):
        """The whole point of Ask #1: a context in allowed_contexts is scraped
        even with zero SkyPilot clusters on it (fresh-onboarding case). No
        cluster state is consulted at all."""
        errors: List[Dict[str, str]] = []
        with self._patch_allowed(['ctx-fresh']), \
             mock.patch.object(debug_utils.global_user_state,
                               'get_cluster_from_name') as get_cluster, \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        return_value=[]) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        dump.assert_called_once()
        assert dump.call_args.kwargs['context'] == 'ctx-fresh'
        # The per-context path no longer derives contexts from clusters.
        get_cluster.assert_not_called()

    def test_dedupes_repeated_contexts(self, tmp_path):
        errors: List[Dict[str, str]] = []
        with self._patch_allowed(['ctx-a', 'ctx-a']), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        return_value=[]) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        dump.assert_called_once()
        assert dump.call_args.kwargs['context'] == 'ctx-a'

    def test_in_cluster_none_maps_to_in_cluster_dir(self, tmp_path):
        """existing_allowed_contexts returns None for in-cluster auth."""
        errors: List[Dict[str, str]] = []
        with self._patch_allowed([None]), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        return_value=[]) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        dump.assert_called_once()
        assert dump.call_args.kwargs['context'] is None
        assert dump.call_args.kwargs['output_dir'].endswith(
            os.path.join('kubernetes_contexts', 'in-cluster'))

    def test_provider_errors_are_prefixed_with_context(self, tmp_path):
        provider_errors = [{
            'resource': 'gpu_metrics',
            'error': 'forbidden',
            'traceback': 'tb',
        }]
        errors: List[Dict[str, str]] = []
        with self._patch_allowed(['ctx-a']), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        return_value=provider_errors):
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        assert len(errors) == 1
        assert errors[0]['component'] == 'kubernetes_contexts'
        # Prefixed with the sanitized context dir name.
        assert errors[0]['resource'].endswith('/gpu_metrics')
        assert errors[0]['resource'].startswith('ctx-a-')
        assert errors[0]['error'] == 'forbidden'

    def test_one_broken_context_does_not_abort_others(self, tmp_path):
        """run_in_parallel re-raises the first exception; the per-context task
        must swallow it so a broken context can't abort the others' dumps."""

        def _dump(context, output_dir):
            del output_dir
            if context == 'ctx-bad':
                raise RuntimeError('context timed out')
            return []

        errors: List[Dict[str, str]] = []
        with self._patch_allowed(['ctx-good', 'ctx-bad']), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources',
                        side_effect=_dump):
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        # The bad context's failure is recorded, the good one still ran.
        assert len(errors) == 1
        assert errors[0]['component'] == 'kubernetes_contexts'
        assert errors[0]['resource'].startswith('ctx-bad-')
        assert 'timed out' in errors[0]['error']

    def test_no_allowed_contexts_is_noop(self, tmp_path):
        errors: List[Dict[str, str]] = []
        with self._patch_allowed([]), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources'
                       ) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)
        dump.assert_not_called()
        assert not (tmp_path / 'kubernetes_contexts').exists()
        assert not errors

    def test_allowed_contexts_lookup_failure_is_recorded(self, tmp_path):
        errors: List[Dict[str, str]] = []
        with mock.patch.object(debug_utils.clouds.Kubernetes,
                               'existing_allowed_contexts',
                               side_effect=RuntimeError('kubeconfig boom')), \
             mock.patch('sky.provision.kubernetes.debug.dump_context_resources'
                       ) as dump:
            debug_utils._dump_kube_contexts_info(str(tmp_path), errors)

        dump.assert_not_called()
        assert len(errors) == 1
        assert errors[0]['resource'] == 'allowed_contexts'
        assert 'boom' in errors[0]['error']
