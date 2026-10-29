"""Write dashboard data straight into the e2e server's DB.

The test process and the server share SKY_RUNTIME_DIR, so these helpers write
the same SQLite files the server reads. Rows are shaped so nothing on the
server picks them up and acts on them: managed jobs have no controller pid and
are never in a scheduler state that the recovery path relaunches.
"""

import json
import re
import time
from typing import Callable, List, Optional

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
        # set_pending records a PENDING event. SQLite reuses the highest
        # deleted job id, so a left-over event would show up in the history
        # of the next job seeded with that id.
        session.execute(
            sqlalchemy.delete(job_state.job_events_table).where(
                job_state.job_events_table.c.spot_job_id == job_id))
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


def wait_for_user_requests(user_id: str, timeout_s: float = 15) -> None:
    """Wait until the server has no pending or running request from this
    user. Returns at the deadline either way; the leftover check reports
    what remains."""
    from sky.server.requests import requests as requests_lib
    live = [
        requests_lib.RequestStatus.PENDING, requests_lib.RequestStatus.RUNNING
    ]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not requests_lib.get_request_tasks(
                requests_lib.RequestTaskFilter(
                    status=live, user_id=user_id, fields=['request_id'])):
            return
        time.sleep(0.5)


def user_exists(user_id: str) -> bool:
    from sky import global_user_state
    return global_user_state.get_user(user_id) is not None


_LEFTOVER_CHECKS: List[Callable[[], List[str]]] = []


def register_leftover_check(check: Callable[[], List[str]]) -> None:
    """Add a check for rows tests created and left behind. A suite that seeds
    its own tables registers one, so exclusive tests also wait for those."""
    _LEFTOVER_CHECKS.append(check)


def leftover_test_rows() -> List[str]:
    """Rows other tests created and have not removed yet. An exclusive test
    starts only when this is empty."""
    out: List[str] = []
    for check in _LEFTOVER_CHECKS:
        out.extend(check())
    return out


def _leftover_jobs() -> List[str]:
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    t = job_state.job_info_table
    with orm.Session(engine) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'managed job {n}' for n in names if is_test_name(n)]


def _leftover_clusters() -> List[str]:
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    t = global_user_state.cluster_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'cluster {n}' for n in names if is_test_name(n)]


def _leftover_cluster_history() -> List[str]:
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    t = global_user_state.cluster_history_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'cluster history {n}' for n in names if is_test_name(n)]


register_leftover_check(_leftover_jobs)
register_leftover_check(_leftover_clusters)
register_leftover_check(_leftover_cluster_history)

# --- clusters ----------------------------------------------------------------


def cluster(name: str,
            user_hash: str,
            status: str = 'UP',
            workspace: str = 'default',
            cpus: str = '1',
            memory: str = '2',
            launched_ago_s: float = 600,
            context: str = 'e2e-ctx') -> str:
    """Insert a Kubernetes cluster row owned by ``user_hash``; returns name.

    The handle is a real CloudVmRayResourceHandle so the server can unpickle
    it. Its context does not exist, and the refresh daemon is off, so nothing
    tries to reach it.
    """
    # pylint: disable=import-outside-toplevel
    import sky
    from sky import global_user_state
    from sky.backends import cloud_vm_ray_backend

    # Launched resources must be launchable (carry an instance type);
    # Kubernetes names instance types <cpus>CPU--<memory>GB.
    resources = sky.Resources(infra=f'kubernetes/{context}',
                              instance_type=f'{cpus}CPU--{memory}GB')
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name=name,
        cluster_name_on_cloud=name,
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=resources)
    global_user_state.add_or_update_cluster(name,
                                            handle,
                                            requested_resources={resources},
                                            ready=status == 'UP',
                                            is_launch=True)
    global_user_state.set_cluster_yaml(
        name, f'cluster_name: {name}\nprovider:\n  type: external\n')
    t = global_user_state.cluster_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.update(t).where(t.c.name == name).values(
                user_hash=user_hash,
                workspace=workspace,
                status=status,
                launched_at=int(time.time() - launched_ago_s)))
        session.commit()
    return name


def delete_cluster(name: str) -> None:
    """Remove a seeded cluster with its history and events.

    ``remove_cluster`` keeps the history row (it only closes the usage
    interval), which the All view and cost reports would still list.
    """
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    if global_user_state.get_cluster_from_name(name) is not None:
        global_user_state.remove_cluster(name, terminate=True)
    purge_cluster(name)


def purge_cluster(name: str) -> None:
    """Remove the history rows and events of a cluster name."""
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    h = global_user_state.cluster_history_table
    e = global_user_state.cluster_event_table
    c = global_user_state.cluster_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        hashes = set(
            session.execute(
                sqlalchemy.select(
                    h.c.cluster_hash).where(h.c.name == name)).scalars().all())
        hashes.update(
            session.execute(
                sqlalchemy.select(
                    c.c.cluster_hash).where(c.c.name == name)).scalars().all())
        hashes.discard(None)
        if hashes:
            session.execute(
                sqlalchemy.delete(e).where(e.c.cluster_hash.in_(hashes)))
        session.execute(sqlalchemy.delete(h).where(h.c.name == name))
        session.commit()


def count_clusters() -> int:
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    t = global_user_state.cluster_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        return session.execute(
            sqlalchemy.select(
                sqlalchemy.func.count()).select_from(t)).scalar_one()
