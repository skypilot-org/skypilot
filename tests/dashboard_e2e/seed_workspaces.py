"""Workspace helpers for the workspaces e2e tests.

Workspaces are server config, not DB rows: tests create and delete them
through the API server (``/workspaces/create``, ``/workspaces/delete``) so the
server's config lock and access policies stay consistent. The server writes
them to the config file named by ``SKYPILOT_GLOBAL_CONFIG``, which this module
reads to find workspaces by name and to report the ones tests left behind.

A workspace's key in ``workspaces:`` is a generated id (``ws-<hex>``); the
name a test gives it (``<uid>-<label>``) is stored as its ``name`` field.
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from framework import seed
import requests
import sqlalchemy
from sqlalchemy import orm
import yaml

from sky.jobs import state as job_state

DEFAULT_WORKSPACE = 'default'


def config_workspaces() -> Dict[str, Dict[str, Any]]:
    """Workspaces in the server's config file, keyed by id. Excludes the
    implicit default workspace unless the config names it."""
    path = os.environ.get('SKYPILOT_GLOBAL_CONFIG')
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    return dict(config.get('workspaces') or {})


def workspace_name(workspace_id: str,
                   workspaces: Optional[Dict[str, Any]] = None) -> str:
    """The user-facing name of a workspace (its id when it has none)."""
    workspaces = config_workspaces() if workspaces is None else workspaces
    return (workspaces.get(workspace_id) or {}).get('name') or workspace_id


def workspace_id(name: str) -> Optional[str]:
    """Id of the workspace named ``name``, or None if there is none."""
    for wid, config in config_workspaces().items():
        if (config or {}).get('name') == name or wid == name:
            return wid
    return None


def _leftover_workspaces() -> List[str]:
    out = []
    for wid, config in config_workspaces().items():
        name = (config or {}).get('name') or wid
        if seed.is_test_name(name):
            out.append(f'workspace {name} ({wid})')
    return out


seed.register_leftover_check(_leftover_workspaces)

# --- API ---------------------------------------------------------------------


def api_result(session: requests.Session,
               resp: requests.Response,
               timeout_s: float = 120) -> Tuple[bool, Any]:
    """Wait for an async request and return (succeeded, value or error).

    ``/api/get`` answers 200 with the return value once the request
    succeeded, and 500 with the error once it failed."""
    assert resp.status_code == 200, (
        f'{resp.request.method} {resp.url}: {resp.status_code} {resp.text}')
    request_id = resp.headers['X-Skypilot-Request-ID']
    base = resp.url.split('/workspaces')[0].split('/api/')[0]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = session.get(f'{base}/api/get',
                        params={'request_id': request_id},
                        timeout=timeout_s)
        if r.status_code == 200:
            body = r.json()
            if body.get('status') == 'SUCCEEDED':
                import json  # pylint: disable=import-outside-toplevel
                value = body.get('return_value')
                return True, json.loads(value) if value else None
            if body.get('status') in ('FAILED', 'CANCELLED'):
                return False, body.get('error')
        elif r.status_code >= 500:
            return False, r.text[:2000]
        time.sleep(0.3)
    return False, f'request {request_id} did not finish in {timeout_s}s'


def create_workspace(session: requests.Session, name: str,
                     config: Dict[str, Any]) -> str:
    """Create a workspace through the API and return its id."""
    ok, value = api_result(
        session,
        session.post(
            f'{session.base_url}/workspaces/create',  # type: ignore[attr-defined]
            json={
                'workspace_name': name,
                'config': config
            },
            timeout=60))
    assert ok, f'creating workspace {name}: {value}'
    for wid, ws_config in (value or {}).items():
        if (ws_config or {}).get('name') == name:
            return wid
    raise AssertionError(f'created workspace {name} is missing from {value}')


def delete_workspace(session: requests.Session, wid: str) -> Tuple[bool, Any]:
    """Delete a workspace and the test rows seeded into it.

    The server refuses to delete a workspace that still has active clusters
    or managed jobs, so this removes those first."""
    delete_rows_in_workspace(wid)
    return api_result(
        session,
        session.post(
            f'{session.base_url}/workspaces/delete',  # type: ignore[attr-defined]
            json={'workspace_name': wid},
            timeout=60))


def delete_rows_in_workspace(wid: str) -> None:
    """Remove the clusters and managed jobs tests seeded into ``wid``."""
    # pylint: disable=import-outside-toplevel
    from sky import global_user_state
    t = global_user_state.cluster_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        names = session.execute(
            sqlalchemy.select(
                t.c.name).where(t.c.workspace == wid)).scalars().all()
    for name in names:
        if seed.is_test_name(name):
            seed.delete_cluster(name)
    j = job_state.job_info_table
    engine = job_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        rows = session.execute(
            sqlalchemy.select(j.c.spot_job_id,
                              j.c.name).where(j.c.workspace == wid)).all()
    for job_id, name in rows:
        if seed.is_test_name(name):
            seed.delete_managed_job(job_id)
