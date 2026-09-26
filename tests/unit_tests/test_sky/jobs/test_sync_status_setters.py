"""Synchronous and asynchronous task transition parity."""
# pylint: disable=redefined-outer-name,protected-access
import contextlib
import datetime
import json
from unittest import mock

import filelock
import pytest
import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky import exceptions
from sky.jobs import runtime
from sky.jobs import state
from sky.skylet import job_lib


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'jobs.db'
    engine = sqlalchemy.create_engine(f'sqlite:///{path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{path}')

    @contextlib.contextmanager
    def db_lock(section):
        with filelock.FileLock(str(tmp_path / f'{section}.lock')):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    monkeypatch.setattr(state.time, 'time', lambda: 300.0)
    with mock.patch.object(state, 'datetime') as clock:
        clock.datetime.now.return_value = datetime.datetime(2026, 1, 1)
        yield engine
    engine.dispose()


def seed(db, job_id, status, **values):
    with db.begin() as conn:
        conn.execute(state.job_info_table.insert().values(
            spot_job_id=job_id, name='task', schedule_state='INACTIVE'))
        conn.execute(state.spot_table.insert().values(spot_job_id=job_id,
                                                      task_id=0,
                                                      task_name='task',
                                                      status=status,
                                                      last_recovered_at=100.0,
                                                      job_duration=50.0,
                                                      recovery_count=2,
                                                      **values))


def snapshot(db, job_id):
    with db.connect() as conn:
        row = dict(
            conn.execute(
                sqlalchemy.select(state.spot_table).where(
                    state.spot_table.c.spot_job_id == job_id)).mappings().one())
        events = [
            dict(event) for event in conn.execute(
                sqlalchemy.select(state.job_events_table).where(
                    state.job_events_table.c.spot_job_id == job_id).order_by(
                        state.job_events_table.c.id)).mappings()
        ]
    row.pop('spot_job_id')
    row.pop('job_id')
    for event in events:
        event.pop('id')
        event.pop('spot_job_id')
    return row, events


CASES = [
    ('starting', 'PENDING', {},
     dict(run_timestamp='run',
          submit_time=110.0,
          resources_str='1x[CPU:1]',
          specs={'cpus': 1},
          full_resources_json={'cpus': '1'})),
    ('starting', 'PENDING', {
        'submitted_at': 10.0
    },
     dict(run_timestamp='run',
          submit_time=110.0,
          resources_str='1x[CPU:1]',
          specs={})),
    ('started', 'STARTING', {}, dict(start_time=120.0)),
    ('started', 'PENDING', {
        'start_at': 10.0,
        'recovering_from_failure': True
    }, dict(start_time=120.0)),
    ('recovering', 'RUNNING', {},
     dict(force_transit_to_recovering=False,
          cluster_event_reason='Node unavailable')),
    ('recovering', 'WINDING_DOWN', {},
     dict(force_transit_to_recovering=True, user_job_failure_reason='Exit 1')),
    ('recovering', 'RECOVERING', {},
     dict(force_transit_to_recovering=True,
          recovery_source=state.RecoverySource.RESTART)),
    ('recovered', 'RECOVERING', {
        'recovering_from_failure': True
    }, dict(recovered_time=200.0)),
    ('recovered', 'RECOVERING', {
        'recovering_from_failure': False
    }, dict(recovered_time=200.0)),
    ('recovered', 'RECOVERING', {},
     dict(recovered_time=200.0, count_recovery=False)),
    ('succeeded', 'RUNNING', {
        'recovering_from_failure': True
    }, dict(end_time=200.0)),
    ('succeeded', 'WINDING_DOWN', {}, dict(end_time=200.0)),
    ('cancelling', 'RUNNING', {}, {}),
    ('cancelling', 'SUCCEEDED', {
        'end_at': 200.0
    }, {}),
    ('cancelled', 'CANCELLING', {
        'recovering_from_failure': True
    }, {}),
    ('cancelled', 'CANCELLED', {
        'end_at': 200.0
    }, {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('transition,initial,values,kwargs', CASES)
async def test_sync_async_parity(db, transition, initial, values, kwargs):
    seed(db, 1, initial, **values)
    seed(db, 2, initial, **values)
    sync_callback = mock.Mock()
    async_callback = mock.AsyncMock()
    task = {} if transition in ('cancelling', 'cancelled') else {'task_id': 0}
    getattr(state, f'set_{transition}')(job_id=1,
                                        callback_func=sync_callback,
                                        **task,
                                        **kwargs)
    await getattr(state,
                  f'set_{transition}_async')(job_id=2,
                                             callback_func=async_callback,
                                             **task,
                                             **kwargs)
    assert snapshot(db, 1) == snapshot(db, 2)
    assert sync_callback.call_args_list == async_callback.await_args_list


@pytest.mark.asyncio
@pytest.mark.parametrize('transition,kwargs', [
    ('starting',
     dict(run_timestamp='run',
          submit_time=110.0,
          resources_str='1x[CPU:1]',
          specs={})),
    ('started', dict(start_time=120.0)),
    ('recovering', dict(force_transit_to_recovering=False)),
    ('recovered', dict(recovered_time=200.0)),
    ('succeeded', dict(end_time=200.0)),
])
async def test_terminal_task_rejects_transition(db, transition, kwargs):
    seed(db, 1, 'SUCCEEDED', end_at=150.0)
    seed(db, 2, 'SUCCEEDED', end_at=150.0)
    callback = mock.Mock()
    async_callback = mock.AsyncMock()
    with pytest.raises(exceptions.ManagedJobStatusError):
        getattr(state, f'set_{transition}')(1,
                                            0,
                                            callback_func=callback,
                                            **kwargs)
    with pytest.raises(exceptions.ManagedJobStatusError):
        await getattr(state,
                      f'set_{transition}_async')(2,
                                                 0,
                                                 callback_func=async_callback,
                                                 **kwargs)
    assert snapshot(db, 1) == snapshot(db, 2)
    callback.assert_not_called()
    async_callback.assert_not_awaited()


@pytest.mark.parametrize(
    'transition,initial,kwargs,expected_count,expected_duration', [
        ('recovered', 'RECOVERING', dict(recovered_time=200.0), 3, 50.0),
        ('recovering', 'RUNNING', dict(force_transit_to_recovering=True), 2,
         250.0),
    ])
def test_sync_commit_lost_retry(db, monkeypatch, transition, initial, kwargs,
                                expected_count, expected_duration):
    seed(db, 1, initial, recovering_from_failure=True)
    committed_updates = []

    class LostCommitSession(orm.Session):
        """Commit an update before raising a transient connection error."""

        status_update = False

        def execute(self, statement, *args, **kwargs):
            if isinstance(statement, sqlalchemy.sql.Update):
                self.status_update = statement.table.name == 'spot'
            return super().execute(statement, *args, **kwargs)

        def commit(self):
            super().commit()
            if self.status_update:
                committed_updates.append(True)
                if len(committed_updates) == 1:
                    raise sqlalchemy.exc.OperationalError(
                        'COMMIT', {}, ConnectionError('connection lost'))

    monkeypatch.setattr(state.orm, 'Session', LostCommitSession)
    monkeypatch.setattr(state.db_retries.time, 'sleep', lambda _: None)
    callback = mock.Mock()
    getattr(state, f'set_{transition}')(1, 0, callback_func=callback, **kwargs)
    row, events = snapshot(db, 1)
    assert len(committed_updates) == 2
    assert row['recovery_count'] == expected_count
    assert row['job_duration'] == expected_duration
    assert len(events) == 1
    callback.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('initial', ['STARTING', 'RUNNING', 'CANCELLING'])
async def test_runtime_observation_parity_and_replay(db, initial):
    metadata = json.dumps(
        {'runtime_recovery': {
            'runtime_id': 'allocation',
            'restarts': 5
        }})
    seed(db, 1, initial, metadata=metadata)
    seed(db, 2, initial, metadata=metadata)
    sync_callback = mock.Mock()
    async_callback = mock.AsyncMock()
    observations = [
        runtime.RuntimeObservation('allocation',
                                   5,
                                   job_lib.JobStatus.RUNNING,
                                   started_at=100.0,
                                   nodes=['node-1']),
        runtime.RuntimeObservation('allocation',
                                   7,
                                   job_lib.JobStatus.PENDING,
                                   recovery_reasons={
                                       6: 'first',
                                       7: 'second'
                                   }),
        runtime.RuntimeObservation('allocation',
                                   7,
                                   job_lib.JobStatus.RUNNING,
                                   started_at=200.0,
                                   nodes=['node-2']),
    ]
    for observation in observations:
        state.observe_runtime(1,
                              0,
                              observation,
                              callback_func=sync_callback,
                              infra={
                                  'cloud': 'test',
                                  'region': 'region'
                              })
        await state.observe_runtime_async(2,
                                          0,
                                          observation,
                                          callback_func=async_callback,
                                          infra={
                                              'cloud': 'test',
                                              'region': 'region'
                                          })
        before = snapshot(db, 1)
        state.observe_runtime(1,
                              0,
                              observation,
                              callback_func=sync_callback,
                              infra={
                                  'cloud': 'test',
                                  'region': 'region'
                              })
        await state.observe_runtime_async(2,
                                          0,
                                          observation,
                                          callback_func=async_callback,
                                          infra={
                                              'cloud': 'test',
                                              'region': 'region'
                                          })
        assert snapshot(db, 1) == before
        assert snapshot(db, 1) == snapshot(db, 2)
    assert sync_callback.call_args_list == async_callback.await_args_list
    row, events = snapshot(db, 1)
    assert row['recovery_count'] == (2 if initial == 'CANCELLING' else 4)
    assert row['status'] == ('CANCELLING'
                             if initial == 'CANCELLING' else 'RUNNING')
    if initial != 'CANCELLING':
        reasons = [
            event['reason']
            for event in events
            if event['new_status'] == 'RECOVERING'
        ]
        assert reasons == ['first', 'second']
        with db.connect() as conn:
            infos = conn.execute(
                sqlalchemy.select(
                    state.job_info_table.c.node_names,
                    state.job_info_table.c.cloud,
                    state.job_info_table.c.region).order_by(
                        state.job_info_table.c.spot_job_id)).all()
        assert infos[0] == infos[1]
        assert infos[0][1:] == ('test', 'region')


def test_runtime_observation_commit_lost_counts_once(db, monkeypatch):
    seed(db, 1, 'RUNNING', metadata='{}')
    commits = []

    class LostCommitSession(orm.Session):

        def commit(self):
            super().commit()
            commits.append(True)
            if len(commits) == 1:
                raise sqlalchemy.exc.OperationalError(
                    'COMMIT', {}, ConnectionError('connection lost'))

    monkeypatch.setattr(state.orm, 'Session', LostCommitSession)
    monkeypatch.setattr(state.db_retries.time, 'sleep', lambda _: None)
    observation = runtime.RuntimeObservation('allocation',
                                             3,
                                             job_lib.JobStatus.RUNNING,
                                             started_at=200.0,
                                             recovery_reasons={
                                                 1: 'first',
                                                 2: 'second',
                                                 3: 'third'
                                             })
    state.observe_runtime(1, 0, observation, callback_func=mock.Mock())
    row, events = snapshot(db, 1)
    assert len(commits) == 1
    assert row['recovery_count'] == 5
    assert json.loads(row['metadata'])['runtime_recovery']['restarts'] == 3
    assert [event['reason'] for event in events
           ] == ['first', 'second', 'third', 'Runtime recovery completed']
