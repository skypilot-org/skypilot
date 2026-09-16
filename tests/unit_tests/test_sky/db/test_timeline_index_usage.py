"""The daemon's two queries must actually reach their indexes.

A partial index is used only while its predicate still covers the query's
filters. Narrow either query, or widen either predicate, and the planner
silently stops using the index -- nothing raises, no test fails, and the daemon
goes back to scanning the whole table once a minute. Asserting the plan is the
only way that shows up as a failure rather than as a slow tenant.
"""
import sqlalchemy
from sqlalchemy import orm

from sky.jobs import state as managed_job_state
from sky.skylet import constants as skylet_constants
from sky.utils.db import db_utils


def _engine(tmp_path, monkeypatch):
    # Without this the manager opens the real runtime directory's database,
    # which is whatever an earlier run left there -- the failure then reads as
    # a missing column rather than as a test pointed at the wrong file.
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))
    return managed_job_state._db_manager.get_engine()


def _plan(engine, statement):
    """SQLite's query plan for a statement, as one lowercase string."""
    compiled = statement.compile(engine, compile_kwargs={'literal_binds': True})
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text(f'EXPLAIN QUERY PLAN {compiled}')).fetchall()
    return ' '.join(str(cell) for row in rows for cell in row).lower()


def _timeline_query():
    # Rebuilt rather than imported: the functions execute, and what is being
    # asserted is the shape of the statement they run.
    spot = managed_job_state.spot_table
    info = managed_job_state.job_info_table
    return sqlalchemy.select(spot.c.spot_job_id, spot.c.start_at).select_from(
        spot.join(info, spot.c.spot_job_id == info.c.spot_job_id,
                  isouter=True)).where(
                      sqlalchemy.and_(
                          spot.c.start_at.is_not(None),
                          spot.c.created_at.is_not(None),
                          spot.c.submitted_at.is_not(None),
                          spot.c.eligible_at.is_not(None),
                          spot.c.t_time_to_running.is_(None),
                      )).order_by(spot.c.start_at).limit(200)


def _never_ran_query():
    spot = managed_job_state.spot_table
    info = managed_job_state.job_info_table
    return sqlalchemy.select(spot.c.spot_job_id, spot.c.end_at).select_from(
        spot.join(info, spot.c.spot_job_id == info.c.spot_job_id,
                  isouter=True)).where(
                      sqlalchemy.and_(
                          spot.c.end_at.is_not(None),
                          spot.c.start_at.is_(None),
                          spot.c.created_at.is_not(None),
                          spot.c.submitted_at.is_not(None),
                          spot.c.eligible_at.is_not(None),
                          spot.c.t_controller_queue.is_(None),
                      )).order_by(spot.c.end_at).limit(200)


def test_the_pending_timeline_query_uses_its_index(tmp_path, monkeypatch):
    plan = _plan(_engine(tmp_path, monkeypatch), _timeline_query())

    assert 'ix_spot_pending_timeline' in plan, plan
    # Ordering served by the index, not by sorting the result afterwards: the
    # LIMIT is only cheap if the scan can stop early.
    assert 'temp b-tree' not in plan, plan


def test_the_never_ran_query_uses_its_index(tmp_path, monkeypatch):
    plan = _plan(_engine(tmp_path, monkeypatch), _never_ran_query())

    assert 'ix_spot_never_ran' in plan, plan
    assert 'temp b-tree' not in plan, plan


def test_the_indexes_exclude_rows_the_queries_cannot_return(
        tmp_path, monkeypatch):
    """The reason they are partial, stated as a count rather than a comment.

    A row that predates the timeline columns has no created_at, so it can never
    satisfy the query and is never given a timeline -- under a full index it
    would sit in the scanned prefix permanently. Here it is simply not indexed.
    """
    engine = _engine(tmp_path, monkeypatch)
    with orm.Session(engine) as session:
        # One pre-upgrade row and one live candidate.
        # Pre-upgrade and never ran: no created_at, no eligible_at, no
        # start_at, and an end_at that sorts it to the front of the never-ran
        # index. The exact row the missing clause used to index forever.
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=0,
            task_name='old',
            status='FAILED',
            start_at=None,
            end_at=20.0))
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=2,
            task_id=0,
            task_name='new',
            status='RUNNING',
            created_at=100.0,
            eligible_at=100.0,
            submitted_at=110.0,
            start_at=200.0))
        session.commit()

    # Both predicates, not just one. They were written out separately once and
    # the second lost its origin clause, which indexed every pre-upgrade row
    # that ended without running -- so asserting only the first is how that
    # returns.
    for predicate in (managed_job_state.PENDING_TIMELINE_PREDICATE,
                      managed_job_state.NEVER_RAN_PREDICATE):
        with engine.connect() as conn:
            indexed = conn.execute(
                sqlalchemy.text(
                    f'SELECT count(*) FROM spot WHERE {predicate}')).scalar()
        assert indexed == 0 or indexed == 1, (predicate, indexed)
        # The pre-upgrade row must never be one of them.
        with engine.connect() as conn:
            stale = conn.execute(
                sqlalchemy.text(
                    'SELECT count(*) FROM spot WHERE spot_job_id = 1 '
                    f'AND ({predicate})')).scalar()
        assert stale == 0, f'a pre-upgrade row is indexed by: {predicate}'
