"""The queries in this package must actually reach their indexes.

Asserted on SQLite, because that is what a unit test has. Postgres decides
partial-index implication in its own planner, so a green run here is evidence
that the predicates line up, not proof that the deployments which actually run
these queries use the index. That is what the EXPLAIN in the PR description is
for.


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


def test_the_unattended_scan_uses_its_index(tmp_path, monkeypatch):
    """The stall scan's claimed half, asserted on the statement it really runs.

    Not a reconstruction: the SQL string is taken from sky/jobs/stall.py, so a
    change there that stops matching the predicate fails here rather than
    quietly going back to scanning every task ever run.
    """
    # pylint: disable=import-outside-toplevel
    from sky.jobs import stall

    engine = _engine(tmp_path, monkeypatch)
    sql = stall._UNATTENDED_SELECT.format(
        claimed_in_flight=managed_job_state.CLAIMED_IN_FLIGHT_PREDICATE,
        now='0.0',
        age_seconds=900,
        candidate_limit=2000)
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text(f'EXPLAIN QUERY PLAN {sql}')).fetchall()
    plan = ' '.join(str(cell) for row in rows for cell in row).lower()

    assert 'ix_spot_unattended' in plan, plan
    # Ordering served by the index rather than by sorting afterwards, so the
    # LIMIT can stop the scan early.
    assert 'temp b-tree' not in plan, plan


def test_the_unattended_index_excludes_work_that_is_done(tmp_path, monkeypatch):
    """The reason it is partial, as a count rather than a comment."""
    engine = _engine(tmp_path, monkeypatch)
    with orm.Session(engine) as session:
        # Claimed and still in flight: the one row the scan can return.
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=0,
            task_name='live',
            status='STARTING',
            submitted_at=100.0))
        # Ran and finished, and a task never claimed at all. Neither can ever
        # match the query, so neither belongs in the index.
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=2,
            task_id=0,
            task_name='done',
            status='SUCCEEDED',
            submitted_at=100.0,
            start_at=110.0,
            end_at=200.0))
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=3, task_id=0, task_name='pending', status='PENDING'))
        session.commit()

    with engine.connect() as conn:
        indexed = conn.execute(
            sqlalchemy.text(
                'SELECT spot_job_id FROM spot WHERE '
                f'{managed_job_state.CLAIMED_IN_FLIGHT_PREDICATE}')).fetchall()

    assert [row[0] for row in indexed] == [1]


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
