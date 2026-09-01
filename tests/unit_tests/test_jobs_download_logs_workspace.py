"""`sky jobs logs --sync-down` must not reach outside the caller's workspaces.

The backend resolves a bare `--sync-down` through `GetAllJobIdsByName`, which
has no workspace filter. `download_logs` therefore resolves the job id itself,
through the queue (which applies `accessible_workspaces`).
"""
from unittest import mock

import pytest

from sky.jobs.server import core


@pytest.fixture
def backend():
    with mock.patch.object(core, '_maybe_restart_controller') as restart, \
         mock.patch.object(core.backend_utils,
                           'get_backend_from_handle') as get_backend:
        restart.return_value = mock.MagicMock()
        # spec= makes the isinstance(...) assert in download_logs pass.
        be = mock.MagicMock(spec=core.backends.CloudVmRayBackend)
        be.sync_down_managed_job_logs.return_value = {1: '/tmp/1'}
        get_backend.return_value = be
        yield be


def _record(job_id, job_name=None):
    return mock.MagicMock(job_id=job_id, job_name=job_name)


def test_bare_sync_down_picks_latest_accessible_not_latest_global(backend):
    """Job 99 exists but is in another workspace, so the queue never returns it."""
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(7),
                                          _record(3)], 2, {}, 2, [])) as queue:
        core.download_logs(name=None,
                           job_id=None,
                           refresh=False,
                           controller=False)

    # Workspace is the boundary, not the user: all_users must stay True.
    assert queue.call_args.kwargs['all_users'] is True
    backend.sync_down_managed_job_logs.assert_called_once()
    kwargs = backend.sync_down_managed_job_logs.call_args.kwargs
    assert kwargs['job_id'] == 7
    # job_name must be cleared, or the backend re-resolves unfiltered.
    assert kwargs['job_name'] is None


def test_job_id_outside_accessible_workspaces_is_refused(backend):
    """An explicit id the queue cannot see must not be downloaded."""
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([], 0, {}, 0, [])):
        assert core.download_logs(name=None,
                                  job_id=99,
                                  refresh=False,
                                  controller=False) == {}
    backend.sync_down_managed_job_logs.assert_not_called()


def test_job_id_inside_accessible_workspaces_is_allowed(backend):
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(42)], 1, {}, 1, [])) as queue:
        core.download_logs(name=None,
                           job_id=42,
                           refresh=False,
                           controller=False)
    assert queue.call_args.kwargs['job_ids'] == [42]
    assert backend.sync_down_managed_job_logs.call_args.kwargs['job_id'] == 42


def test_name_matches_exactly_and_picks_latest(backend):
    """`name_match` is fuzzy, so a near-miss name must not be selected."""
    records = [_record(5, 'train'), _record(9, 'train-v2'), _record(6, 'train')]
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=(records, 3, {}, 3, [])):
        core.download_logs(name='train',
                           job_id=None,
                           refresh=False,
                           controller=False)
    assert backend.sync_down_managed_job_logs.call_args.kwargs['job_id'] == 6


def test_no_accessible_job_returns_empty(backend):
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([], 0, {}, 0, [])):
        assert core.download_logs(name=None,
                                  job_id=None,
                                  refresh=False,
                                  controller=False) == {}
    backend.sync_down_managed_job_logs.assert_not_called()


# --- streaming (`sky jobs logs`, no --sync-down) ---
#
# Same hole as --sync-down: the controller resolves via `get_latest_job_id` /
# `get_nonterminal_job_ids_by_name`, neither of which filters by workspace.


@pytest.fixture
def runner():
    with mock.patch.object(core, '_maybe_restart_controller') as restart, \
         mock.patch.object(core.backend_utils,
                           'get_backend_from_handle') as get_backend, \
         mock.patch.object(core.managed_job_runner, 'current') as current:
        restart.return_value = mock.MagicMock()
        get_backend.return_value = mock.MagicMock(
            spec=core.backends.CloudVmRayBackend)
        r = mock.MagicMock()
        r.tail_managed_job_logs.return_value = 0
        current.return_value = r
        yield r


def _tail(**kw):
    args = dict(name=None,
                job_id=None,
                follow=False,
                controller=False,
                refresh=False)
    args.update(kw)
    return core.tail_logs(**args)


def test_tail_bare_picks_latest_accessible_not_latest_global(runner):
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(7),
                                          _record(3)], 2, {}, 2, [])) as queue:
        _tail()
    assert queue.call_args.kwargs['all_users'] is True
    kwargs = runner.tail_managed_job_logs.call_args.kwargs
    assert kwargs['job_id'] == 7
    assert kwargs['job_name'] is None


def test_tail_job_id_outside_accessible_workspaces_is_refused(runner):
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([], 0, {}, 0, [])):
        assert _tail(job_id=99) == core.exceptions.JobExitCode.NOT_FOUND
    runner.tail_managed_job_logs.assert_not_called()


def test_name_lookup_is_status_scoped_but_bare_lookup_is_not(runner):
    """`skip_finished` mirrors the *name* lookup only.

    Master resolves a bare `sky jobs logs` via `get_latest_job_id`, which has
    no status filter, while `-n <name>` goes through
    `get_nonterminal_job_ids_by_name`. Applying the status filter to the bare
    path would stop the newest job being tailable as soon as it finished.
    """
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(4, 'n')], 1, {}, 1,
                                         [])) as queue:
        _tail(name='n')
    assert queue.call_args.kwargs['skip_finished'] is True

    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(4, 'n')], 1, {}, 1,
                                         [])) as queue:
        _tail(name='n', controller=True)
    assert queue.call_args.kwargs['skip_finished'] is False

    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(4)], 1, {}, 1, [])) as queue:
        _tail()
    assert 'skip_finished' not in queue.call_args.kwargs


def test_bare_tail_does_not_announce_a_download(runner):
    """The sync-down notice must not leak into the streaming path."""
    with mock.patch.object(core, 'queue_v2_api',
                           return_value=([_record(9), _record(4)], 2, {}, 2, [])), \
         mock.patch.object(core.logger, 'info') as log_info:
        _tail()
    msg = ' '.join(str(c.args[0]) for c in log_info.call_args_list)
    assert 'Downloading' not in msg
    assert runner.tail_managed_job_logs.call_args.kwargs['job_id'] == 9


def test_tail_ambiguous_name_raises_like_master(runner):
    """Ambiguity must behave exactly as before: raise, do not guess.

    Wording and exception type match `managed_job_utils.stream_logs`, which
    this resolution replaces -- workspace scoping is the only intended change.
    """
    records = [_record(5, 'train'), _record(6, 'train')]
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=(records, 2, {}, 2, [])):
        with pytest.raises(ValueError, match='Multiple running jobs found'):
            _tail(name='train')
        with pytest.raises(ValueError, match=r'Job IDs: 6, 5'):
            _tail(name='train', controller=True)
    runner.tail_managed_job_logs.assert_not_called()


def test_not_found_message_matches_master(runner):
    with mock.patch.object(core, 'queue_v2_api', return_value=([], 0, {}, 0, [])), \
         mock.patch.object(core.logger, 'info') as log_info:
        _tail(name='nope')
    msg = ' '.join(str(c.args[0]) for c in log_info.call_args_list)
    assert "No running managed job found with name 'nope'." in msg


def test_bare_lookup_is_bounded_not_a_whole_table_scan(runner):
    """It replaces a `LIMIT 1` query; an unbounded fetch is a real regression.

    Pagination here is by unique job, and the query already defaults to job_id
    descending, so page/limit alone give the newest job.
    """
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(7)], 1, {}, 1, [])) as queue:
        _tail()
    kwargs = queue.call_args.kwargs
    # Two, not one: sync-down has to be able to say "more than one job exists",
    # which a limit of 1 would make unanswerable.
    assert kwargs['limit'] == 2 and kwargs['page'] == 1
    # The name path must stay unbounded: `--controller` ambiguity has to list
    # every matching id, so a limit there would truncate the message.
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=([_record(7, 'n')], 1, {}, 1,
                                         [])) as queue:
        _tail(name='n')
    assert 'limit' not in queue.call_args.kwargs


def test_sync_down_multiple_notice_reads_as_a_sentence(backend):
    """Without a name the "Multiple jobs IDs found" clause must not appear."""
    with mock.patch.object(core, 'queue_v2_api',
                           return_value=([_record(9), _record(4)], 2, {}, 2, [])), \
         mock.patch.object(core.logger, 'info') as log_info:
        core.download_logs(name=None,
                           job_id=None,
                           refresh=False,
                           controller=False)
    msg = ' '.join(str(c.args[0]) for c in log_info.call_args_list)
    assert 'Multiple jobs IDs found Downloading' not in msg
    assert 'Downloading the latest job logs.' in msg

    with mock.patch.object(core, 'queue_v2_api',
                           return_value=([_record(9, 'n'), _record(4, 'n')], 2,
                                         {}, 2, [])), \
         mock.patch.object(core.logger, 'info') as log_info:
        core.download_logs(name='n',
                           job_id=None,
                           refresh=False,
                           controller=False)
    msg = ' '.join(str(c.args[0]) for c in log_info.call_args_list)
    assert 'Multiple jobs IDs found under the name n. Downloading' in msg


def test_multi_task_job_is_one_job_not_an_ambiguity(runner):
    """The queue returns a record per task, so one job can repeat its id.

    Without de-duplication a multi-task job looks like several jobs and log
    streaming raises a spurious ambiguity error. The lookups this replaces
    select `spot_job_id` DISTINCT.
    """
    two_tasks = [_record(7, 'multi'), _record(7, 'multi')]
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=(two_tasks, 2, {}, 2, [])):
        _tail(name='multi')
    assert runner.tail_managed_job_logs.call_args.kwargs['job_id'] == 7


def test_multi_task_job_does_not_announce_multiple_on_sync_down(backend):
    """Same de-duplication on the sync-down path: one job, no 'Multiple' notice."""
    two_tasks = [_record(7, 'multi'), _record(7, 'multi')]
    with mock.patch.object(core, 'queue_v2_api',
                           return_value=(two_tasks, 2, {}, 2, [])), \
         mock.patch.object(core.logger, 'info') as log_info:
        core.download_logs(name='multi',
                           job_id=None,
                           refresh=False,
                           controller=False)
    msg = ' '.join(str(c.args[0]) for c in log_info.call_args_list)
    assert 'Multiple' not in msg
    assert backend.sync_down_managed_job_logs.call_args.kwargs['job_id'] == 7


def test_tail_name_matches_exactly(runner):
    """`name_match` is fuzzy, so 'train-v2' must not satisfy `-n train`."""
    records = [_record(9, 'train-v2'), _record(5, 'train')]
    with mock.patch.object(core,
                           'queue_v2_api',
                           return_value=(records, 2, {}, 2, [])):
        _tail(name='train')
    assert runner.tail_managed_job_logs.call_args.kwargs['job_id'] == 5
