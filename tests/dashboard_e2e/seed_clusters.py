"""Extra seeding for the clusters pages: autostop, launch command and YAML,
cluster events, terminated (history) clusters, and workspaces.

``framework.seed.cluster`` writes the live cluster row. The helpers here add
what the list and detail pages read on top of it. Everything they write is
marked so the exclusive guard can see leftovers:

- history rows a test creates on purpose are named ``<uid>-hist...``;
- event reasons start with ``EVENT_PREFIX``;
- workspaces are named ``<uid>-...``.
"""
import json
import pickle
import time
from typing import Any, Dict, List, Optional

from framework import seed
import requests
import sqlalchemy
from sqlalchemy import orm

EVENT_PREFIX = 'e2e-event: '


def _gus():
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    return global_user_state


def _engine():
    return _gus()._db_manager.get_engine()  # pylint: disable=protected-access


def cluster_hash(name: str) -> Optional[str]:
    t = _gus().cluster_table
    with orm.Session(_engine()) as session:
        return session.execute(
            sqlalchemy.select(
                t.c.cluster_hash).where(t.c.name == name)).scalar_one_or_none()


def set_details(name: str,
                autostop: Optional[int] = None,
                to_down: bool = False,
                command: Optional[str] = None,
                task_yaml: Optional[str] = None) -> None:
    """Autostop, and the command and YAML the cluster was launched with."""
    gus = _gus()
    if autostop is not None:
        gus.set_cluster_autostop_value(name, autostop, to_down)
    values: Dict[str, Any] = {}
    if command is not None:
        values['last_creation_command'] = command
    if task_yaml is not None:
        values['last_creation_yaml'] = task_yaml
    if values:
        t = gus.cluster_table
        with orm.Session(_engine()) as session:
            session.execute(
                sqlalchemy.update(t).where(t.c.name == name).values(**values))
            session.commit()


def add_event(name: str,
              reason: str,
              status: str = 'UP',
              ago_s: float = 60) -> str:
    """Record a status-change event; returns the full reason shown."""
    # pylint: disable=import-outside-toplevel
    from sky.utils import status_lib
    gus = _gus()
    full = EVENT_PREFIX + reason
    gus.add_cluster_event(name,
                          status_lib.ClusterStatus[status],
                          full,
                          gus.ClusterEventType.STATUS_CHANGE,
                          transitioned_at=int(time.time() - ago_s))
    return full


def terminate_into_history(name: str, user_hash: str, workspace: str,
                           started_ago_s: float, ended_ago_s: float) -> str:
    """Tear a seeded cluster down so only its history row is left, owned by
    ``user_hash`` and up from ``started_ago_s`` to ``ended_ago_s``. Returns
    the cluster hash (the history detail page is keyed by it)."""
    assert '-hist' in name, 'history clusters must be named <uid>-hist...'
    gus = _gus()
    h = cluster_hash(name)
    assert h is not None, f'cluster {name} must be seeded first'
    gus.remove_cluster(name, terminate=True)
    now = time.time()
    start, end = int(now - started_ago_s), int(now - ended_ago_s)
    t = gus.cluster_history_table
    with orm.Session(_engine()) as session:
        session.execute(
            sqlalchemy.update(t).where(t.c.cluster_hash == h).values(
                user_hash=user_hash,
                workspace=workspace,
                launched_at=start,
                usage_intervals=pickle.dumps([(start, end)]),
                last_activity_time=end))
        session.commit()
    return h


def purge(name: str) -> None:
    """Remove the history rows and events a cluster name left behind."""
    seed.purge_cluster(name)


def _leftover_events() -> List[str]:
    e = _gus().cluster_event_table
    with orm.Session(_engine()) as session:
        reasons = session.execute(
            sqlalchemy.select(e.c.reason).where(
                e.c.reason.like(EVENT_PREFIX + '%'))).scalars().all()
    return [f'cluster event {r}' for r in reasons]


seed.register_leftover_check(_leftover_events)

# --- workspaces (through the API: they live in the server's config) --------


def _wait_request(api: requests.Session, resp: requests.Response) -> Any:
    assert resp.status_code == 200, f'{resp.status_code} {resp.text}'
    rid = resp.headers.get('X-Skypilot-Request-ID')
    deadline = time.time() + 120
    while time.time() < deadline:
        r = api.get(
            f'{api.base_url}/api/get',  # type: ignore[attr-defined]
            params={'request_id': rid},
            timeout=60)
        if r.status_code == 200:
            return r.json().get('return_value')
        if r.status_code >= 500 or r.status_code in (400, 403, 404):
            raise AssertionError(f'request {rid} failed: {r.status_code} '
                                 f'{r.text[:500]}')
        time.sleep(0.5)
    raise AssertionError(f'request {rid} did not finish')


def list_workspaces(api: requests.Session) -> Dict[str, Any]:
    r = api.get(
        f'{api.base_url}/workspaces',  # type: ignore[attr-defined]
        timeout=60)
    return json.loads(_wait_request(api, r) or '{}')


def create_workspace(api: requests.Session, name: str,
                     config: Dict[str, Any]) -> str:
    """Create a workspace as admin; returns its id."""
    r = api.post(
        f'{api.base_url}/workspaces/create',  # type: ignore[attr-defined]
        json={
            'workspace_name': name,
            'config': config
        },
        timeout=60)
    _wait_request(api, r)
    for ws_id, cfg in list_workspaces(api).items():
        if cfg.get('name') == name or ws_id == name:
            return ws_id
    raise AssertionError(f'workspace {name} was not created')


def delete_workspace(api: requests.Session, ws_id: str) -> None:
    r = api.post(
        f'{api.base_url}/workspaces/delete',  # type: ignore[attr-defined]
        json={'workspace_name': ws_id},
        timeout=60)
    _wait_request(api, r)
