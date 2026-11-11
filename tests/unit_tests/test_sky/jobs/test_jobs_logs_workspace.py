"""Managed job logs are read only from workspaces the caller can read.

Runs against a real temporary SQLite jobs DB (fixture pattern from
test_cancel_events.py); the runner and backend are stubbed at the call
boundary, so the tests see which job the logs would be read for.
"""
import contextlib
import subprocess
from typing import Any, Dict
from unittest import mock

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import backends
from sky import exceptions
from sky.jobs import runner as managed_job_runner
from sky.jobs import state
from sky.jobs.server import core

_NOT_FOUND = exceptions.JobExitCode.NOT_FOUND


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        with filelock.FileLock(str(tmp_path / f'.{_section}.lock'), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


_UNSET = object()


def _seed(engine,
          job_id,
          name,
          workspace=_UNSET,
          status='RUNNING',
          num_tasks=1):
    """One job. workspace=None is a NULL column; _UNSET means no job_info."""
    with engine.connect() as conn:
        if workspace is not _UNSET:
            conn.execute(state.job_info_table.insert().values(
                spot_job_id=job_id, name=name, workspace=workspace))
        for task_id in range(num_tasks):
            conn.execute(state.spot_table.insert().values(
                spot_job_id=job_id,
                task_id=task_id,
                task_name=name,
                job_name=name,
                status=status,
                submitted_at=float(job_id)))
        conn.commit()


class TestStateWorkspaceFilter:

    def test_none_keeps_every_workspace(self, jobs_db):
        _seed(jobs_db, 1, 'a', 'ws-a')
        _seed(jobs_db, 2, 'a', 'ws-b')
        assert state.get_latest_job_id() == 2
        assert state.get_all_job_ids_by_name('a') == [2, 1]
        assert state.get_nonterminal_job_ids_by_name('a') == [2, 1]

    def test_filters_to_the_given_workspaces(self, jobs_db):
        _seed(jobs_db, 1, 'a', 'ws-a')
        _seed(jobs_db, 2, 'a', 'ws-b')
        assert state.get_latest_job_id(workspaces={'ws-a'}) == 1
        assert state.get_all_job_ids_by_name('a', workspaces={'ws-a'}) == [1]
        assert state.get_all_job_ids_by_name(None, workspaces={'ws-a'}) == [1]
        assert state.get_nonterminal_job_ids_by_name('a', workspaces={'ws-a'
                                                                     }) == [1]

    def test_empty_set_matches_nothing(self, jobs_db):
        _seed(jobs_db, 1, 'a', 'default')
        assert state.get_latest_job_id(workspaces=set()) is None
        assert state.get_all_job_ids_by_name(None, workspaces=set()) == []
        assert state.get_nonterminal_job_ids_by_name('a',
                                                     workspaces=set()) == []

    @pytest.mark.parametrize('workspace', [None, _UNSET],
                             ids=['null-workspace', 'no-job-info-row'])
    def test_missing_workspace_counts_as_default(self, jobs_db, workspace):
        # As get_workspace answers for the same job.
        _seed(jobs_db, 1, 'a', workspace)
        assert state.get_workspace(1) == 'default'
        assert state.get_latest_job_id(workspaces={'default'}) == 1
        assert state.get_all_job_ids_by_name(None,
                                             workspaces={'default'}) == [1]
        assert state.get_latest_job_id(workspaces={'ws-a'}) is None
        assert state.get_all_job_ids_by_name(None, workspaces={'ws-a'}) == []


class _Runner:

    def __init__(self):
        self.calls = []

    def tail_managed_job_logs(self, **kwargs):
        self.calls.append(kwargs)
        return 0


@pytest.fixture
def server(jobs_db, monkeypatch):
    """Consolidation mode; the runner and backend record what they get."""
    monkeypatch.setattr(core.managed_job_utils, 'is_consolidation_mode',
                        lambda: True)
    monkeypatch.setattr(core, '_maybe_restart_controller',
                        lambda *args, **kwargs: mock.Mock())
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    backend.sync_down_managed_job_logs.return_value = {'1': '/tmp/x'}
    monkeypatch.setattr(core.backend_utils, 'get_backend_from_handle',
                        lambda handle: backend)
    runner = _Runner()
    monkeypatch.setattr(managed_job_runner, '_current', runner)
    # ws-a is hidden from the scoped caller, ws-b is theirs. The hidden job is
    # the newest, so a global "latest" lookup would pick it.
    _seed(jobs_db, 1, 'mine', 'ws-b')
    _seed(jobs_db, 2, 'shared', 'ws-b')
    _seed(jobs_db, 3, 'shared', 'ws-a')
    _seed(jobs_db, 4, 'secret', 'ws-a')
    return mock.Mock(runner=runner, backend=backend, engine=jobs_db)


def _readable(monkeypatch, names):
    monkeypatch.setattr(core.workspaces_core, 'get_readable_workspace_names',
                        lambda: names)


def _tail(**kwargs):
    args: Dict[str, Any] = dict(name=None,
                                job_id=None,
                                follow=False,
                                controller=False,
                                refresh=False)
    args.update(kwargs)
    return core.tail_logs(**args)


def _sync_down(**kwargs):
    args: Dict[str, Any] = dict(name=None,
                                job_id=None,
                                refresh=False,
                                controller=False)
    args.update(kwargs)
    return core.download_logs(**args)


class TestTailLogs:

    @pytest.fixture(autouse=True)
    def _scoped(self, server, monkeypatch):
        _readable(monkeypatch, {'ws-b'})

    def _streamed(self, server):
        assert len(server.runner.calls) == 1, server.runner.calls
        call = server.runner.calls[0]
        assert call['job_name'] is None
        return call['job_id']

    def test_hidden_id_answers_as_missing(self, server, capsys):
        assert _tail(job_id=4) == _NOT_FOUND
        assert 'Job 4 not found.' in capsys.readouterr().out
        assert server.runner.calls == []

    def test_readable_id_is_streamed(self, server):
        _tail(job_id=1)
        assert self._streamed(server) == 1

    def test_bare_picks_the_newest_readable_job(self, server):
        _tail()
        assert self._streamed(server) == 2

    def test_hidden_name_answers_as_missing(self, server, capsys):
        assert _tail(name='secret') == _NOT_FOUND
        assert ("No running managed job found with name 'secret'."
                in capsys.readouterr().out)
        assert server.runner.calls == []

    def test_name_shared_with_a_hidden_job_is_not_ambiguous(self, server):
        _tail(name='shared')
        assert self._streamed(server) == 2

    def test_controller_name_includes_finished_jobs(self, server):
        _seed(server.engine, 5, 'done', 'ws-b', status='SUCCEEDED')
        assert _tail(name='done') == _NOT_FOUND
        _tail(name='done', controller=True)
        assert self._streamed(server) == 5

    def test_multi_task_job_is_one_job(self, server):
        _seed(server.engine, 5, 'multi', 'ws-b', num_tasks=3)
        _tail(name='multi')
        assert self._streamed(server) == 5

    @pytest.mark.parametrize('controller', [False, True])
    def test_ambiguity_is_written_to_the_stream(self, server, capsys,
                                                controller):
        _seed(server.engine, 5, 'mine', 'ws-b')
        # In the stream with code 1, as from `stream_logs` run by `python -c`:
        # a raise here would not reach `--no-follow` or SDK follow=False.
        assert _tail(name='mine', controller=controller) == 1
        assert 'Multiple' in capsys.readouterr().out
        assert server.runner.calls == []

    def test_empty_readable_set_sees_nothing(self, server, monkeypatch):
        _readable(monkeypatch, set())
        assert _tail(job_id=1) == _NOT_FOUND
        assert _tail(name='mine') == _NOT_FOUND
        assert _tail() == _NOT_FOUND
        assert server.runner.calls == []
        assert _sync_down(job_id=1) == {}
        assert _sync_down() == {}
        server.backend.sync_down_managed_job_logs.assert_not_called()

    def test_hidden_parent_of_a_dynamic_task(self, server, monkeypatch, capsys):
        # A dynamic task's job is in its parent's workspace.
        _seed(server.engine, 5, 'child', 'ws-a')

        def _resolve_job_task(job_id, task, **kwargs):
            del kwargs  # Unused.
            assert (job_id, task) == (4, 2)
            return 5, None  # Job 4's dynamic task 2 is job 5.

        monkeypatch.setattr(core, '_resolve_job_task', _resolve_job_task)
        assert _tail(job_id=4, task=2) == _NOT_FOUND
        # Named by the id asked for, not the task's own job id.
        assert 'Job 4 not found.' in capsys.readouterr().out
        assert server.runner.calls == []

    def test_resolution_never_forks_or_dumps_the_queue(self, server,
                                                       monkeypatch):
        # #10614 resolved through the queue: a `python -c` per log request
        # under consolidation, which stalled the server under concurrent tails.
        def _forbidden(*args, **kwargs):
            raise AssertionError('log resolution must read the DB only')

        monkeypatch.setattr(core, 'queue_v2', _forbidden)
        monkeypatch.setattr(core, 'queue_v2_api', _forbidden)
        monkeypatch.setattr(core._DefaultManagedJobRunner,
                            'fetch_managed_job_table', _forbidden)
        server.backend.run_on_head.side_effect = _forbidden
        monkeypatch.setattr(subprocess, 'Popen', _forbidden)
        for kwargs in (dict(job_id=1), dict(job_id=4), dict(name='shared'),
                       dict(name='mine', controller=True), dict()):
            _tail(**kwargs)
            _sync_down(**kwargs)


class TestUnchangedPaths:
    """Callers this does not scope get exactly what they asked for."""

    @pytest.mark.parametrize('scoped,consolidation', [(False, True),
                                                      (True, False)],
                             ids=['unscoped', 'dedicated-controller'])
    def test_passes_the_request_through(self, server, monkeypatch, scoped,
                                        consolidation):
        _readable(monkeypatch, {'ws-b'} if scoped else None)
        if not consolidation:
            # Dedicated controller: not even the permission lookup runs.
            monkeypatch.setattr(
                core.workspaces_core, 'get_readable_workspace_names',
                mock.Mock(side_effect=AssertionError('no permission lookup')))
        monkeypatch.setattr(core.managed_job_utils, 'is_consolidation_mode',
                            lambda: consolidation)
        lookup = mock.Mock(side_effect=AssertionError('no lookup'))
        monkeypatch.setattr(core.managed_job_state, 'get_workspace', lookup)
        monkeypatch.setattr(core.managed_job_state, 'get_latest_job_id', lookup)

        _tail(name='secret')
        assert server.runner.calls[-1]['job_name'] == 'secret'
        assert server.runner.calls[-1]['job_id'] is None
        _tail(job_id=4)
        assert server.runner.calls[-1]['job_id'] == 4

        _sync_down(name='secret')
        kwargs = server.backend.sync_down_managed_job_logs.call_args.kwargs
        assert (kwargs['job_id'], kwargs['job_name']) == (None, 'secret')


class TestDownloadLogs:

    @pytest.fixture(autouse=True)
    def _scoped(self, server, monkeypatch):
        _readable(monkeypatch, {'ws-b'})

    def _downloaded(self, server):
        kwargs = server.backend.sync_down_managed_job_logs.call_args.kwargs
        assert kwargs['job_name'] is None
        return kwargs['job_id']

    def test_hidden_id_downloads_nothing(self, server):
        assert _sync_down(job_id=4) == {}
        server.backend.sync_down_managed_job_logs.assert_not_called()

    def test_hidden_name_downloads_nothing(self, server):
        assert _sync_down(name='secret') == {}
        server.backend.sync_down_managed_job_logs.assert_not_called()

    def test_readable_id(self, server):
        _sync_down(job_id=1)
        assert self._downloaded(server) == 1

    def test_bare_picks_the_newest_readable_job(self, server):
        _sync_down()
        assert self._downloaded(server) == 2

    def test_name_takes_the_newest_readable_match(self, server):
        _seed(server.engine, 5, 'shared', 'ws-b', status='SUCCEEDED')
        _sync_down(name='shared')
        assert self._downloaded(server) == 5
