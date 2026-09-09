"""Unit tests for managed jobs client SDK queue helpers."""
from unittest import mock

import pytest

from sky.jobs import constants as managed_job_constants
from sky.jobs.client import sdk as jobs_sdk
from sky.jobs.client import sdk_async as jobs_sdk_async
from sky.schemas.api import responses


def _unwrap(fn):
    """Strip all functools.wraps-based decorators."""
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _call_raw_queue_v2(**kwargs):
    """Call queue_v2 (decorators stripped) and return the request JSON body."""
    raw_queue_v2 = _unwrap(jobs_sdk.queue_v2)
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=999), \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           return_value='response') as mock_request, \
         mock.patch.object(jobs_sdk.server_common,
                           'get_request_id',
                           return_value='request-id'):
        raw_queue_v2(**kwargs)
    _, request_kwargs = mock_request.call_args
    return request_kwargs['json']


def test_queue_v2_defaults_to_lightweight_fields():
    # A high remote API version avoids the version-based field stripping so we
    # can assert the full default field set is sent.
    body = _call_raw_queue_v2(refresh=False)
    assert body['fields'] == list(
        managed_job_constants.DEFAULT_MANAGED_JOB_FIELDS)
    # The lightweight default must not pull heavy fields.
    assert 'node_names' not in body['fields']
    assert 'metadata' not in body['fields']


def test_queue_v2_fields_none_requests_all_fields():
    # fields=None is the explicit "give me everything" escape hatch.
    body = _call_raw_queue_v2(refresh=False, fields=None)
    assert body['fields'] is None


def test_queue_version_2_dispatches_to_queue_v2():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk, 'queue_v2',
                           return_value='request-id-v2') as mock_queue_v2:
        result = raw_queue(refresh=True,
                           skip_finished=True,
                           all_users=True,
                           job_ids=[1, 2],
                           version=2)

    assert result == 'request-id-v2'
    mock_queue_v2.assert_called_once_with(refresh=True,
                                          skip_finished=True,
                                          all_users=True,
                                          job_ids=[1, 2])


def test_queue_version_1_warns_and_uses_legacy_endpoint():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           return_value='response') as mock_request, \
         mock.patch.object(jobs_sdk.server_common,
                           'get_request_id',
                           return_value='request-id-v1') as mock_get_request_id, \
         mock.patch.object(jobs_sdk.logger, 'warning') as mock_warning:
        result = raw_queue(refresh=False,
                           skip_finished=True,
                           all_users=False,
                           job_ids=[3],
                           version=1)

    assert result == 'request-id-v1'
    mock_warning.assert_called_once()
    assert 'is deprecated and will be removed in v0.13' in mock_warning.call_args.args[
        0]
    mock_request.assert_called_once()
    args, kwargs = mock_request.call_args
    assert args == ('POST', '/jobs/queue')
    assert kwargs['json']['refresh'] is False
    assert kwargs['json']['skip_finished'] is True
    assert kwargs['json']['all_users'] is False
    assert kwargs['json']['job_ids'] == [3]
    mock_get_request_id.assert_called_once_with(response='response')


def test_queue_invalid_version_raises():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with pytest.raises(ValueError, match='Must be 1 or 2'):
        raw_queue(refresh=False, version=3)


@pytest.mark.asyncio
async def test_async_queue_passes_version_through():

    async def mock_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with mock.patch('sky.jobs.client.sdk_async.asyncio.to_thread',
                    side_effect=mock_to_thread), \
         mock.patch.object(jobs_sdk, 'queue',
                           return_value='request-id') as mock_queue, \
         mock.patch.object(jobs_sdk_async.sdk_async,
                           '_stream_and_get',
                           new=mock.AsyncMock(
                               return_value='queue-result')) as mock_stream:
        result = await jobs_sdk_async.queue(refresh=True,
                                            skip_finished=False,
                                            all_users=True,
                                            job_ids=[9],
                                            version=2)

    assert result == 'queue-result'
    mock_queue.assert_called_once_with(True, False, True, [9], 2)
    mock_stream.assert_called_once_with(
        'request-id', jobs_sdk_async.sdk_async.DEFAULT_STREAM_CONFIG)


class TestResolveJobGroup:
    """`launch(job_group=...)` → _JobGroupAttachment."""

    _GROUP_ENV = {
        'SKYPILOT_JOBGROUP_NAME': 'rl-run',
        'SKYPILOT_MANAGED_JOB_ID': '42',
        'SKYPILOT_ROOT_JOB_ID': '42',
        'SKYPILOT_TASK_ID': 'sky-managed-2026-09-09-19-44-13-977203_rl-run_'
                            'watcher_42-1',
    }

    def _set_env(self, monkeypatch, **overrides):
        env = dict(self._GROUP_ENV, **overrides)
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)

    def test_auto_inside_job_group_attaches(self, monkeypatch):
        self._set_env(monkeypatch)
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                42, 1, 42, auto=True))

    def test_auto_inside_dynamic_member_keeps_the_top_level_root(
            self, monkeypatch):
        # A job launched from an eval (itself a plain job attached to group
        # 42, so no SKYPILOT_JOBGROUP_NAME): the controller marked the eval's
        # task with root 42, so the launch attaches under 42.
        self._set_env(monkeypatch,
                      SKYPILOT_JOBGROUP_NAME=None,
                      SKYPILOT_MANAGED_JOB_ID='57',
                      SKYPILOT_ROOT_JOB_ID='42',
                      SKYPILOT_TASK_ID='sky-managed-2026_eval_57-0')
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                57, 0, 42, auto=True))

    def test_auto_without_root_marker_does_not_attach(self, monkeypatch):
        # No SKYPILOT_ROOT_JOB_ID means the controller did not put this job in
        # a tree (or predates the marker): top-level, even with the job group
        # name present.
        self._set_env(monkeypatch, SKYPILOT_ROOT_JOB_ID=None)
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                auto=True))

    def test_auto_inside_plain_managed_job_does_not_attach(self, monkeypatch):
        # A plain top-level managed job: the controller sets no root marker,
        # so nested launches keep today's behavior and stay top-level.
        self._set_env(monkeypatch,
                      SKYPILOT_JOBGROUP_NAME=None,
                      SKYPILOT_ROOT_JOB_ID=None)
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                auto=True))

    def test_auto_outside_any_job(self, monkeypatch):
        for k in self._GROUP_ENV:
            monkeypatch.delenv(k, raising=False)
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                auto=True))

    def test_auto_without_task_suffix_leaves_task_unknown(self, monkeypatch):
        self._set_env(monkeypatch, SKYPILOT_TASK_ID='sky-2026-09-09_rl-run_42')
        assert jobs_sdk._resolve_job_group(
            jobs_sdk.AUTO_JOB_GROUP) == (jobs_sdk._JobGroupAttachment(
                42, None, 42, auto=True))

    def test_none_opts_out_even_inside_group(self, monkeypatch):
        self._set_env(monkeypatch)
        assert jobs_sdk._resolve_job_group(
            None) == jobs_sdk._JobGroupAttachment()

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            jobs_sdk._resolve_job_group(True)

    @staticmethod
    def _record(job_id, job_name='j', root_job_id=None):
        return responses.ManagedJobRecord(job_id=job_id,
                                          job_name=job_name,
                                          root_job_id=root_job_id)

    def _resolve_with(self, value, records):
        with mock.patch.object(jobs_sdk, 'queue_v2',
                               return_value='req') as mock_queue, \
             mock.patch.object(jobs_sdk.sdk, 'get',
                               return_value=(records, 0, {}, 0)):
            result = jobs_sdk._resolve_job_group(value)
        mock_queue.assert_called_once()
        return result, mock_queue.call_args.kwargs

    def test_explicit_id_reads_root_from_record(self):
        # Attaching to a top-level job: it is its own root.
        result, kwargs = self._resolve_with(7, [self._record(7)])
        assert result == jobs_sdk._JobGroupAttachment(7, None, 7)
        assert kwargs['job_ids'] == [7]
        # Attaching to a dynamic member: root is the member's root.
        result, _ = self._resolve_with('57', [self._record(57, root_job_id=42)])
        assert result == jobs_sdk._JobGroupAttachment(57, None, 42)

    def test_explicit_unknown_id_raises(self):
        with pytest.raises(ValueError, match='No managed job 9'):
            self._resolve_with(9, [])

    def test_name_resolves_unique_running_job(self):
        # 'trainer-v2' also contains 'trainer', so the server returns it too;
        # only the exact name counts.
        records = [self._record(5, 'trainer'), self._record(6, 'trainer-v2')]
        result, kwargs = self._resolve_with('trainer', records)
        assert result == jobs_sdk._JobGroupAttachment(5, None, 5)
        # Server narrows by substring; the exact match happens here.
        assert kwargs['skip_finished'] is True
        assert kwargs['name_match'] == 'trainer'

    def test_name_dedupes_task_records_of_one_job(self):
        # The queue returns one record per task, so a two-task job group named
        # 'trainer' comes back as two records with job_id 5. That is one job,
        # not an ambiguous name.
        records = [self._record(5, 'trainer'), self._record(5, 'trainer')]
        result, _ = self._resolve_with('trainer', records)
        assert result == jobs_sdk._JobGroupAttachment(5, None, 5)

    def test_name_missing_or_ambiguous_raises(self):
        with pytest.raises(ValueError, match='No running managed job'):
            self._resolve_with('nope', [self._record(5, 'x')])
        with pytest.raises(ValueError, match='2 running managed jobs'):
            self._resolve_with('dup',
                               [self._record(5, 'dup'),
                                self._record(9, 'dup')])
