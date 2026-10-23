"""Write dashboard data straight into the e2e server's DB.

The test process and the server share SKY_RUNTIME_DIR, so these helpers write
the same SQLite files the server reads. Rows are shaped so nothing on the
server picks them up and acts on them: managed jobs have no controller pid and
are never in a scheduler state that the recovery path relaunches.
"""

import json
import re
import time
from typing import Optional

import sqlalchemy
from sqlalchemy import orm

from sky.jobs import state as job_state

_TERMINAL = {
    'SUCCEEDED', 'FAILED', 'CANCELLED', 'FAILED_SETUP', 'FAILED_NO_RESOURCE'
}


def managed_job(
    name: str,
    user_hash: str,
    status: str = 'RUNNING',
    workspace: str = 'default',
    resources: str = '1x[CPU:1]',
    submitted_ago_s: float = 600,
) -> int:
    """Insert one single-task managed job and return its id."""
    job_id = job_state.set_job_info_without_job_id(
        name=name,
        workspace=workspace,
        entrypoint=f'sky jobs launch -n {name} -- echo e2e',
        pool=None,
        pool_hash=None,
        user_hash=user_hash,
    )
    job_state.set_pending(job_id, 0, name, resources, json.dumps({}))
    now = time.time()
    values = {
        'status': status,
        'submitted_at': now - submitted_ago_s,
        'resources': resources,
    }
    if status != 'PENDING':
        values['start_at'] = now - submitted_ago_s + 30
    if status in _TERMINAL:
        values['end_at'] = now - 60
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.update(job_state.spot_table).where(
                job_state.spot_table.c.spot_job_id == job_id).values(**values))
        if status in _TERMINAL:
            session.execute(
                sqlalchemy.update(job_state.job_info_table).where(
                    job_state.job_info_table.c.spot_job_id == job_id).values(
                        schedule_state=job_state.ManagedJobScheduleState.DONE.
                        value))
        session.commit()
    return job_id


def delete_managed_job(job_id: int,
                       engine: Optional[sqlalchemy.engine.Engine] = None
                      ) -> None:
    engine = engine or job_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.delete(job_state.spot_table).where(
                job_state.spot_table.c.spot_job_id == job_id))
        session.execute(
            sqlalchemy.delete(job_state.job_info_table).where(
                job_state.job_info_table.c.spot_job_id == job_id))
        session.commit()


def count_managed_jobs() -> int:
    """Top-level managed jobs across all users: what "N jobs in total" counts."""
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    t = job_state.job_info_table
    with orm.Session(engine) as session:
        return session.execute(
            sqlalchemy.select(sqlalchemy.func.count()).where(
                t.c.parent_job_id.is_(None))).scalar_one()


def count_managed_jobs_by_status() -> dict:
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    t = job_state.spot_table
    with orm.Session(engine) as session:
        rows = session.execute(
            sqlalchemy.select(
                t.c.status,
                sqlalchemy.func.count(sqlalchemy.distinct(
                    t.c.spot_job_id))).group_by(t.c.status)).all()
    return dict(rows)


def is_test_name(name: Optional[str]) -> bool:
    """Names tests create start with the ``uid`` fixture: t + 6 hex + '-'."""
    return bool(name) and re.match(r't[0-9a-f]{6}-', name) is not None


def leftover_test_rows() -> list:
    """Rows other tests created and have not removed yet. An exclusive test
    starts only when this is empty."""
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    t = job_state.job_info_table
    with orm.Session(engine) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'managed job {n}' for n in names if is_test_name(n)]
