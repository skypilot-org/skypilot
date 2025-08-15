"""Unit tests for the jobs server queue."""
import time
from typing import Any, Dict, List, Optional

import pytest

from sky.jobs import state as managed_job_state
from sky.jobs import utils as jobs_utils
# Target under test
from sky.jobs.server import core as jobs_core
from sky.skylet import constants as skylet_constants


def _make_job(job_id: int,
              user_name: Optional[str] = 'alice',
              workspace: Optional[str] = 'ws',
              job_name: Optional[str] = 'train',
              pool: Optional[str] = 'default',
              status_obj: Any = None) -> Dict[str, Any]:
    status = status_obj
    if status is None:

        class DummyStatus:

            def is_terminal(self):
                return False

        status = DummyStatus()
    return {
        'job_id': job_id,
        'user_name': user_name,
        'workspace': workspace,
        'job_name': job_name,
        'pool': pool,
        'status': status,
    }


class TestFilterJobs:

    def test_filter_jobs_no_filters_returns_all_and_total(self):
        jobs = [_make_job(i) for i in range(5)]
        filtered, total = jobs_utils.filter_jobs(jobs, None, None, None, None,
                                                 None)
        assert total == 5
        assert len(filtered) == 5
        assert [j['job_id'] for j in filtered] == list(range(0, 5))

    def test_filter_jobs_match_filters(self):
        jobs = [
            _make_job(1, workspace='ws-a', job_name='aa', pool='p1'),
            _make_job(2, workspace='ws-b', job_name='ab', pool='p2'),
            _make_job(3, workspace='ws-b', job_name='ba', pool='p2'),
        ]

        # workspace match
        filtered, total = jobs_utils.filter_jobs(jobs, '-b', None, None, None,
                                                 None)
        assert total == 2
        assert [j['job_id'] for j in filtered] == [2, 3]

        # job name match
        filtered, total = jobs_utils.filter_jobs(jobs, None, 'a', None, None,
                                                 None)
        assert total == 3
        assert [j['job_id'] for j in filtered] == [1, 2, 3]

        # pool match
        filtered, total = jobs_utils.filter_jobs(jobs, None, None, '2', None,
                                                 None)
        assert total == 2
        assert [j['job_id'] for j in filtered] == [2, 3]

    def test_filter_jobs_pagination(self):
        jobs = [_make_job(i) for i in range(1, 26)]
        # page 1, limit 10
        page1, total = jobs_utils.filter_jobs(jobs, None, None, None, 1, 10)
        assert total == 25
        assert [j['job_id'] for j in page1] == list(range(1, 11))

        # page 3, limit 10
        page3, total = jobs_utils.filter_jobs(jobs, None, None, None, 3, 10)
        assert total == 25
        # Remaining 5 items
        assert [j['job_id'] for j in page3] == list(range(21, 26))

        # invalid offset/limit combinations in helper should raise assertion
        with pytest.raises(AssertionError):
            jobs_utils.filter_jobs(jobs, None, None, None, 1, None)

    def test_filter_jobs_missing_keys_and_empty_values(self):
        jobs = [
            {},  # completely empty
            {
                'job_id': 1,
            },  # empty user_name
            {
                'job_id': 2,
            },  # minimal user case
            {
                'job_id': 3,
                'workspace': ''
            },  # empty workspace
            {
                'job_id': 4,
                'job_name': 'train'
            },
            {
                'job_id': 5,
                'pool': None
            },
        ]

        # With workspace match
        filtered, total = jobs_utils.filter_jobs(jobs, None, 'ws', None, None,
                                                 None)
        assert total == 0

        # With pool match
        filtered, total = jobs_utils.filter_jobs(jobs, None, None, 'p2', None,
                                                 None)
        assert total == 0

        # With job name match
        filtered, total = jobs_utils.filter_jobs(jobs, None, 'tr', None, None,
                                                 None)
        assert total == 1
        assert [j['job_id'] for j in filtered] == [4]

    def test_filter_jobs_non_string_values(self):
        jobs = [
            {
                'job_id': 1,
                'pool': 1234,
                'workspace': 'w',
                'job_name': 'j',
                'status': type('S', (), {'is_terminal': lambda self: False})()
            },
            {
                'job_id': 2,
                'pool': 56,
                'workspace': 'w',
                'job_name': 'j',
                'status': type('S', (), {'is_terminal': lambda self: False})()
            },
        ]
        # pool as int should be cast to str and matched by startswith
        filtered, total = jobs_utils.filter_jobs(jobs, None, None, '12', None,
                                                 None)
        assert total == 1
        assert [j['job_id'] for j in filtered] == [1]


class TestQueue:

    def _patch_backend_and_utils(self, monkeypatch: pytest.MonkeyPatch,
                                 jobs: List[Dict[str, Any]]):
        # Create a dummy backend class and patch CloudVmRayBackend to it
        class DummyCloudVmRayBackend:  # acts as the expected base
            pass

        class DummyBackend(DummyCloudVmRayBackend):

            def run_on_head(self,
                            handle,
                            code,
                            require_outputs,
                            stream_logs,
                            separate_stderr=False):
                # Simulate a successful return and echo back the 'code' payload
                return 0, code, ''

        class DummyHandle:
            pass

        def fake_maybe_restart_controller(refresh, stopped_message,
                                          spinner_message):
            return DummyHandle()

        def fake_get_backend_from_handle(handle):
            return DummyBackend()

        def fake_get_workspaces():
            # Allow all workspaces
            # Maintain the structure: mapping of workspace name -> anything
            workspaces = {
                j.get('workspace', skylet_constants.SKYPILOT_DEFAULT_WORKSPACE)
                for j in jobs
            }
            return {w: {} for w in workspaces}

        def fake_get_job_table(skip_finished, accessible_workspaces, job_ids,
                               workspace_match, name_match, pool_match, offset,
                               limit, user_hashes):
            # Return a payload containing all args for the loader to consume
            return {
                'skip_finished': skip_finished,
                'accessible_workspaces': accessible_workspaces,
                'job_ids': job_ids,
                'workspace_match': workspace_match,
                'name_match': name_match,
                'pool_match': pool_match,
                'offset': offset,
                'limit': limit,
                'user_hashes': user_hashes,
            }

        def fake_load_managed_job_queue(payload):
            # Start from provided jobs
            result = list(jobs)

            # 1) Filter by accessibility
            accessible = set(payload.get('accessible_workspaces') or [])
            if accessible:
                result = [j for j in result if j.get('workspace') in accessible]

            # 2) Filter by explicit job_ids
            job_ids = payload.get('job_ids')
            if job_ids:
                result = [j for j in result if j.get('job_id') in set(job_ids)]

            # 3) Filter by user hashes if provided
            user_hashes = payload.get('user_hashes')
            if user_hashes is not None:
                result = [
                    j for j in result if j.get('user_hash') in set(user_hashes)
                ]

            # 4) Skip-finished semantics: include all tasks of jobs that have any non-terminal task
            if payload.get('skip_finished'):
                active_job_ids = set()
                by_job = {}
                for j in result:
                    by_job.setdefault(j.get('job_id'), []).append(j)
                for jid, items in by_job.items():
                    if any(not getattr(it.get('status'), 'is_terminal')()
                           for it in items):
                        active_job_ids.add(jid)
                result = [
                    j for j in result if j.get('job_id') in active_job_ids
                ]

            # 5) Apply name/workspace/pool filters and pagination using the shared helper
            workspace_match = payload.get('workspace_match')
            name_match = payload.get('name_match')
            pool_match = payload.get('pool_match')
            offset = payload.get('offset')
            limit = payload.get('limit')

            filtered, total = jobs_utils.filter_jobs(result, workspace_match,
                                                     name_match, pool_match,
                                                     offset, limit)

            # Return as server queue() does: (jobs, total)
            return filtered, total, jobs_utils.ManagedJobQueueResultType.DICT

        # Patch symbols used by queue()
        monkeypatch.setattr(jobs_core.backends,
                            'CloudVmRayBackend',
                            DummyCloudVmRayBackend,
                            raising=True)
        monkeypatch.setattr(jobs_core, '_maybe_restart_controller',
                            fake_maybe_restart_controller)
        monkeypatch.setattr(jobs_core.backend_utils, 'get_backend_from_handle',
                            fake_get_backend_from_handle)
        monkeypatch.setattr(jobs_core.workspaces_core, 'get_workspaces',
                            fake_get_workspaces)

        # Patch codegen to return a payload and loader to compute results
        monkeypatch.setattr(jobs_core.managed_job_utils.ManagedJobCodeGen,
                            'get_job_table', fake_get_job_table)
        monkeypatch.setattr(jobs_core.managed_job_utils,
                            'load_managed_job_queue',
                            fake_load_managed_job_queue)

    def test_queue_returns_filtered_and_total(self, monkeypatch):
        jobs = [
            _make_job(1, workspace='w1', job_name='a', pool='p'),
            _make_job(2, workspace='w2', job_name='b', pool='p'),
            _make_job(3, workspace='w2', job_name='ab', pool='q'),
        ]
        # Attach user_hash for new user_match semantics: jobs 1 and 3 belong to 'hashA'
        jobs[0]['user_hash'] = 'hashA'
        jobs[1]['user_hash'] = 'hashB'
        jobs[2]['user_hash'] = 'hashA'
        self._patch_backend_and_utils(monkeypatch, jobs)

        # Patch name -> user hash resolution; 'a' should resolve to ['hashA']
        monkeypatch.setattr(jobs_core.global_user_state,
                            'get_user_by_name_match',
                            lambda pattern: [type('U', (), {'id': 'hashA'})()])

        # Filter by user match 'a', page 1, limit 10
        filtered, total = jobs_core.queue(
            refresh=False,
            skip_finished=False,
            all_users=True,
            job_ids=None,
            user_match='a',
            workspace_match=None,
            name_match=None,
            pool_match=None,
            offset=None,
            limit=10,
        )
        # queue() returns Tuple[List[Dict], int]
        assert total == 2
        assert [j['job_id'] for j in filtered] == [1, 3]

    def test_queue_user_match_none(self, monkeypatch):
        jobs = [_make_job(i, workspace='ws') for i in range(1, 31)]
        self._patch_backend_and_utils(monkeypatch, jobs)

        # Patch get_user_by_name_match to return empty list
        monkeypatch.setattr(jobs_core.global_user_state,
                            'get_user_by_name_match', lambda pattern: [])

        filtered, total = jobs_core.queue(
            refresh=False,
            skip_finished=False,
            all_users=True,
            job_ids=None,
            user_match="test",
            workspace_match=None,
            name_match=None,
            pool_match=None,
            offset=None,
            limit=10,
        )
        # When user_match returns no users, should return empty list and total 0
        assert total == 0
        assert len(filtered) == 0

    def test_queue_pagination(self, monkeypatch):
        jobs = [_make_job(i, workspace='ws') for i in range(1, 31)]
        self._patch_backend_and_utils(monkeypatch, jobs)

        # Page 2, limit 10
        filtered, total = jobs_core.queue(
            refresh=False,
            skip_finished=False,
            all_users=True,
            job_ids=None,
            user_match=None,
            workspace_match='ws',
            name_match=None,
            pool_match=None,
            offset=2,
            limit=10,
        )
        assert total == 30
        assert [j['job_id'] for j in filtered] == list(range(11, 21))

    def test_queue_offset_limit_value_errors(self, monkeypatch):
        jobs = [_make_job(1)]
        self._patch_backend_and_utils(monkeypatch, jobs)
        # offset without limit
        with pytest.raises(ValueError):
            jobs_core.queue(refresh=False,
                            skip_finished=False,
                            all_users=True,
                            job_ids=None,
                            user_match=None,
                            workspace_match=None,
                            name_match=None,
                            pool_match=None,
                            offset=1,
                            limit=None)
        # invalid offset
        with pytest.raises(ValueError):
            jobs_core.queue(refresh=False,
                            skip_finished=False,
                            all_users=True,
                            job_ids=None,
                            user_match=None,
                            workspace_match=None,
                            name_match=None,
                            pool_match=None,
                            offset=0,
                            limit=10)
        # invalid limit
        with pytest.raises(ValueError):
            jobs_core.queue(refresh=False,
                            skip_finished=False,
                            all_users=True,
                            job_ids=None,
                            user_match=None,
                            workspace_match=None,
                            name_match=None,
                            pool_match=None,
                            offset=1,
                            limit=0)

    def test_queue_all_users_filtering(self, monkeypatch):
        # Create jobs with user_hash values
        class Status:

            def is_terminal(self):
                return False

        jobs = [
            {
                'job_id': 1,
                'user_name': 'a',
                'workspace': 'w',
                'job_name': 'j',
                'pool': 'p',
                'user_hash': 'me',
                'status': Status()
            },
            {
                'job_id': 2,
                'user_name': 'b',
                'workspace': 'w',
                'job_name': 'j',
                'pool': 'p',
                'user_hash': 'other',
                'status': Status()
            },
            {
                'job_id': 3,
                'user_name': 'c',
                'workspace': 'w',
                'job_name': 'j',
                'pool': 'p',
                'user_hash': None,
                'status': Status()
            },
        ]
        self._patch_backend_and_utils(monkeypatch, jobs)
        # Only my hash and None should pass when all_users=False
        monkeypatch.setattr(jobs_core.common_utils, 'get_user_hash',
                            lambda: 'me')
        filtered, total = jobs_core.queue(refresh=False,
                                          skip_finished=False,
                                          all_users=False,
                                          job_ids=None,
                                          user_match=None,
                                          workspace_match=None,
                                          name_match=None,
                                          pool_match=None,
                                          offset=None,
                                          limit=None)
        assert total == 2
        assert sorted([j['job_id'] for j in filtered]) == [1, 3]

    def test_queue_workspace_filtering(self, monkeypatch):
        jobs = [
            _make_job(1, workspace='w1'),
            _make_job(2, workspace='w2'),
        ]

        # Only include workspace w1
        def fake_get_workspaces_only_w1():
            return {'w1': {}}

        self._patch_backend_and_utils(monkeypatch, jobs)
        monkeypatch.setattr(jobs_core.workspaces_core, 'get_workspaces',
                            fake_get_workspaces_only_w1)
        filtered, total = jobs_core.queue(refresh=False,
                                          skip_finished=False,
                                          all_users=True,
                                          job_ids=None,
                                          user_match=None,
                                          workspace_match=None,
                                          name_match=None,
                                          pool_match=None,
                                          offset=None,
                                          limit=None)
        assert total == 1
        assert [j['job_id'] for j in filtered] == [1]

    def test_queue_skip_finished_includes_all_tasks_of_active_jobs(
            self, monkeypatch):

        class Running:

            def is_terminal(self):
                return False

        class Finished:

            def is_terminal(self):
                return True

        jobs = [
            {
                'job_id': 1,
                'user_name': 'a',
                'workspace': 'w',
                'job_name': 'j',
                'pool': 'p',
                'status': Running()
            },
            {
                'job_id': 1,
                'user_name': 'a',
                'workspace': 'w',
                'job_name': 'j2',
                'pool': 'p',
                'status': Finished()
            },
            {
                'job_id': 2,
                'user_name': 'b',
                'workspace': 'w',
                'job_name': 'k',
                'pool': 'p',
                'status': Finished()
            },
        ]
        self._patch_backend_and_utils(monkeypatch, jobs)
        filtered, total = jobs_core.queue(refresh=False,
                                          skip_finished=True,
                                          all_users=True,
                                          job_ids=None,
                                          user_match=None,
                                          workspace_match=None,
                                          name_match=None,
                                          pool_match=None,
                                          offset=None,
                                          limit=None)
        # Job id 1 has a running task, so both its tasks are included. Job id 2 excluded.
        assert total == 2
        assert sorted([j['job_id'] for j in filtered]) == [1, 1]

    def test_queue_filter_by_job_ids(self, monkeypatch):
        jobs = [_make_job(1), _make_job(2), _make_job(3)]
        self._patch_backend_and_utils(monkeypatch, jobs)
        filtered, total = jobs_core.queue(refresh=False,
                                          skip_finished=False,
                                          all_users=True,
                                          job_ids=[2, 3],
                                          user_match=None,
                                          workspace_match=None,
                                          name_match=None,
                                          pool_match=None,
                                          offset=None,
                                          limit=None)
        assert total == 2
        assert sorted([j['job_id'] for j in filtered]) == [2, 3]


class TestDumpManagedJobQueue:

    def _make_test_job(self, job_id: int, **kwargs) -> Dict[str, Any]:
        """Create a test job with default values."""
        defaults = {
            'job_id': job_id,
            'task_name': f'task_{job_id}',
            'job_name': f'job_{job_id}',
            'workspace': 'default',
            'pool': 'default',
            'status': managed_job_state.ManagedJobStatus.PENDING,
            'schedule_state': managed_job_state.ManagedJobScheduleState.WAITING,
            'priority': 1,
            'user_hash': 'user1',
            'last_recovered_at': time.time(),
            'job_duration': 0,
            'end_at': None,
            'failure_reason': None,
        }
        defaults.update(kwargs)
        return defaults

    def _patch_dependencies(self, monkeypatch: pytest.MonkeyPatch,
                            jobs: List[Dict[str, Any]]):
        """Patch dependencies for dump_managed_job_queue."""

        def fake_get_managed_jobs():
            return jobs

        def fake_get_pool_from_job_id(job_id):
            return None

        def fake_get_pool_submit_info(job_id):
            return f'cluster-{job_id}', {}

        def fake_generate_managed_job_cluster_name(task_name, job_id):
            return f'cluster-{job_id}'

        def fake_get_handle_from_cluster_name(cluster_name):
            return None

        # Patch the dependencies
        monkeypatch.setattr(jobs_utils.managed_job_state, 'get_managed_jobs',
                            fake_get_managed_jobs)
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_pool_from_job_id', fake_get_pool_from_job_id)
        monkeypatch.setattr(jobs_utils.managed_job_state,
                            'get_pool_submit_info', fake_get_pool_submit_info)
        monkeypatch.setattr(jobs_utils, 'generate_managed_job_cluster_name',
                            fake_generate_managed_job_cluster_name)
        monkeypatch.setattr(jobs_utils.global_user_state,
                            'get_handle_from_cluster_name',
                            fake_get_handle_from_cluster_name)

    def test_dump_managed_job_queue_basic(self, monkeypatch):
        """Test basic functionality without filters."""
        jobs = [
            self._make_test_job(1),
            self._make_test_job(2),
            self._make_test_job(3),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 3
        assert len(decoded['jobs']) == 3
        assert [j['job_id'] for j in decoded['jobs']] == [1, 2, 3]

    def test_dump_managed_job_queue_with_filters(self, monkeypatch):
        """Test filtering by workspace, name, and pool."""
        jobs = [
            self._make_test_job(1,
                                workspace='ws1',
                                job_name='job_a',
                                pool='pool1'),
            self._make_test_job(2,
                                workspace='ws2',
                                job_name='job_b',
                                pool='pool1'),
            self._make_test_job(3,
                                workspace='ws1',
                                job_name='job_c',
                                pool='pool2'),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(workspace_match='ws1',
                                                   name_match='job_a',
                                                   pool_match='pool1')
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 1
        assert len(decoded['jobs']) == 1
        assert decoded['jobs'][0]['job_id'] == 1

    def test_dump_managed_job_queue_with_pagination(self, monkeypatch):
        """Test pagination functionality."""
        jobs = [self._make_test_job(i) for i in range(1, 11)]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(offset=2, limit=3)
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 10
        assert len(decoded['jobs']) == 3
        assert [j['job_id'] for j in decoded['jobs']] == [4, 5, 6]

    def test_dump_managed_job_queue_with_user_hashes(self, monkeypatch):
        """Test filtering by user hashes."""
        jobs = [
            self._make_test_job(1, user_hash='user1'),
            self._make_test_job(2, user_hash='user2'),
            self._make_test_job(3, user_hash='user1'),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(user_hashes=['user1'])
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 2
        assert len(decoded['jobs']) == 2
        assert [j['job_id'] for j in decoded['jobs']] == [1, 3]

    def test_dump_managed_job_queue_with_accessible_workspaces(
            self, monkeypatch):
        """Test filtering by accessible workspaces."""
        jobs = [
            self._make_test_job(1, workspace='ws1'),
            self._make_test_job(2, workspace='ws2'),
            self._make_test_job(3, workspace='ws1'),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(
            accessible_workspaces=['ws1'])
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 2
        assert len(decoded['jobs']) == 2
        assert [j['job_id'] for j in decoded['jobs']] == [1, 3]

    def test_dump_managed_job_queue_with_job_ids(self, monkeypatch):
        """Test filtering by specific job IDs."""
        jobs = [self._make_test_job(i) for i in range(1, 6)]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(job_ids=[2, 4])
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 2
        assert len(decoded['jobs']) == 2
        assert [j['job_id'] for j in decoded['jobs']] == [2, 4]

    def test_dump_managed_job_queue_skip_finished(self, monkeypatch):
        """Test skip_finished functionality."""
        jobs = [
            self._make_test_job(
                1, status=managed_job_state.ManagedJobStatus.RUNNING),
            self._make_test_job(
                2, status=managed_job_state.ManagedJobStatus.SUCCEEDED),
            self._make_test_job(
                3, status=managed_job_state.ManagedJobStatus.FAILED),
            self._make_test_job(
                4, status=managed_job_state.ManagedJobStatus.PENDING),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(skip_finished=True)
        decoded = jobs_utils.message_utils.decode_payload(result)

        # Should only include non-terminal statuses (RUNNING, PENDING)
        assert decoded['total'] == 2
        assert len(decoded['jobs']) == 2
        job_ids = [j['job_id'] for j in decoded['jobs']]
        assert 1 in job_ids
        assert 4 in job_ids

    def test_dump_managed_job_queue_priority_blocking(self, monkeypatch):
        """Test priority blocking logic."""
        jobs = [
            self._make_test_job(1,
                                priority=10,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.LAUNCHING),
            self._make_test_job(2,
                                priority=5,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.WAITING),
            self._make_test_job(3,
                                priority=1,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.WAITING),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        # Job 3 should have details about waiting for higher priority jobs
        job3 = next(j for j in decoded['jobs'] if j['job_id'] == 3)
        assert 'Waiting for higher priority jobs to launch' in job3['details']

    def test_dump_managed_job_queue_job_duration_calculation(self, monkeypatch):
        """Test job duration calculation."""
        current_time = time.time()
        jobs = [
            self._make_test_job(1,
                                last_recovered_at=current_time,
                                job_duration=60,
                                end_at=current_time + 120),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        job = decoded['jobs'][0]
        # job_duration should be calculated as end_at - (last_recovered_at - job_duration)
        expected_duration = (current_time + 120) - (current_time - 60)
        assert abs(job['job_duration'] -
                   expected_duration) < 1  # Allow small time differences

    def test_dump_managed_job_queue_recovering_job(self, monkeypatch):
        """Test job duration calculation for recovering jobs."""
        jobs = [
            self._make_test_job(
                1,
                status=managed_job_state.ManagedJobStatus.RECOVERING,
                job_duration=30),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        job = decoded['jobs'][0]
        # For recovering jobs, job_duration should be exactly the stored value
        assert job['job_duration'] == 30

    def test_dump_managed_job_queue_empty_result(self, monkeypatch):
        """Test when no jobs match the filters."""
        jobs = [self._make_test_job(1, workspace='ws1')]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue(workspace_match='ws2')
        decoded = jobs_utils.message_utils.decode_payload(result)

        assert decoded['total'] == 0
        assert len(decoded['jobs']) == 0

    def test_dump_managed_job_queue_with_failure_reason(self, monkeypatch):
        """Test job details with failure reason."""
        jobs = [
            self._make_test_job(1, failure_reason='Test failure'),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        job = decoded['jobs'][0]
        # When job has both state_details and failure_reason, they are combined
        assert 'Test failure' in job['details']
        assert 'Waiting for other jobs to launch' in job['details']

    def test_dump_managed_job_queue_failure_reason_only(self, monkeypatch):
        """Test job details with only failure reason (no state details)."""
        jobs = [
            self._make_test_job(
                1,
                failure_reason='Test failure',
                schedule_state=managed_job_state.ManagedJobScheduleState.ALIVE),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        job = decoded['jobs'][0]
        # When only failure_reason exists (no state_details), it should be prefixed with 'Failure: '
        assert job['details'] == 'Failure: Test failure'

    def test_dump_managed_job_queue_alive_backoff_state(self, monkeypatch):
        """Test ALIVE_BACKOFF schedule state details."""
        jobs = [
            self._make_test_job(1,
                                schedule_state=managed_job_state.
                                ManagedJobScheduleState.ALIVE_BACKOFF),
        ]
        self._patch_dependencies(monkeypatch, jobs)

        result = jobs_utils.dump_managed_job_queue()
        decoded = jobs_utils.message_utils.decode_payload(result)

        job = decoded['jobs'][0]
        assert 'In backoff, waiting for resources' in job['details']
