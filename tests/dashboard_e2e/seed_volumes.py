"""Volumes and Kubernetes infra for the Volumes and Infra page tests.

Volumes are written straight into the server's volume table, owned by a test
user. They name a Kubernetes context that does not exist and the volume
refresh daemon is off, so nothing reconciles them; deleting one for real fails
the way it does when the cluster is gone, which is what lets a test drive the
"Force remove" path.

The Infra page lists Kubernetes contexts from two places: the server's
kubeconfig (which contexts exist) and the cached result of the last
``sky check`` (whether Kubernetes is enabled, per workspace). ``infra_context``
writes a kubeconfig with one context whose API server is unreachable and
marks Kubernetes enabled, which is the state a server is in after a cluster
goes away and before anyone re-runs the check. Both are server-wide, so only
exclusive tests use it, and it restores both afterwards.
"""
import json
import os
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from framework import seed
import pytest
import requests
import sqlalchemy
from sqlalchemy import orm
import yaml

from sky import global_user_state
from sky import models
from sky.clouds import cloud as sky_cloud
from sky.utils import status_lib

# --- volumes -----------------------------------------------------------------


def volume(name: str,
           user_hash: str,
           status: str = 'READY',
           context: str = 'e2e-ctx',
           size: str = '10',
           workspace: str = 'default',
           namespace: str = 'default',
           storage_class: str = 'standard',
           access_mode: str = 'ReadWriteOnce',
           error_message: Optional[str] = None,
           usedby_clusters: Optional[List[str]] = None,
           last_attached_ago_s: Optional[float] = 300) -> str:
    """Insert one Kubernetes PVC volume owned by ``user_hash``."""
    config = models.VolumeConfig(name=name,
                                 type='k8s-pvc',
                                 cloud='kubernetes',
                                 region=context,
                                 zone=None,
                                 name_on_cloud=f'{name}-pvc',
                                 size=size,
                                 config={
                                     'namespace': namespace,
                                     'storage_class_name': storage_class,
                                     'access_mode': access_mode,
                                 })
    global_user_state.add_volume(name,
                                 config,
                                 status_lib.VolumeStatus(status),
                                 error_message=error_message)
    values: Dict[str, Any] = {'user_hash': user_hash, 'workspace': workspace}
    if last_attached_ago_s is not None:
        values['last_attached_at'] = int(time.time() - last_attached_ago_s)
    if usedby_clusters is not None:
        values['usedby_clusters'] = json.dumps(usedby_clusters)
    t = global_user_state.volume_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.update(t).where(t.c.name == name).values(**values))
        session.commit()
    return name


def volume_exists(name: str) -> bool:
    return global_user_state.get_volume_by_name(name) is not None


@pytest.fixture
def seed_volume() -> Iterator[Callable[..., str]]:
    """Insert volumes for this test and delete whatever is left afterwards."""
    names: List[str] = []

    def make(*args, **kwargs) -> str:
        name = volume(*args, **kwargs)
        names.append(name)
        return name

    yield make
    for name in names:
        global_user_state.delete_volume(name)


def _leftover_volumes() -> List[str]:
    t = global_user_state.volume_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        names = session.execute(sqlalchemy.select(t.c.name)).scalars().all()
    return [f'volume {n}' for n in names if seed.is_test_name(n)]


# --- infra: kubeconfig and the cached check result ---------------------------

# What `sky check` caches per workspace (global_user_state config table).
_CHECK_KEY_PREFIXES = ('enabled_clouds_', 'check_results_', 'allowed_clouds_')


def server_kubeconfig_path() -> str:
    """The kubeconfig the server reads (run_server.sh sets KUBECONFIG)."""
    return (os.environ.get('E2E_KUBECONFIG') or
            os.path.join(os.environ['E2E_RUNTIME_DIR'], 'kubeconfig'))


def _check_rows() -> Dict[str, str]:
    t = global_user_state.config_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        rows = session.execute(sqlalchemy.select(t.c.key, t.c.value)).all()
    return {k: v for k, v in rows if k.startswith(_CHECK_KEY_PREFIXES)}


def _restore_check_rows(saved: Dict[str, str]) -> None:
    t = global_user_state.config_table
    engine = global_user_state._db_manager.get_engine()  # pylint: disable=protected-access
    with orm.Session(engine) as session:
        for key in set(_check_rows()) - set(saved):
            session.execute(sqlalchemy.delete(t).where(t.c.key == key))
        for key, value in saved.items():
            session.execute(sqlalchemy.delete(t).where(t.c.key == key))
            session.execute(sqlalchemy.insert(t).values(key=key, value=value))
        session.commit()


def _kubeconfig(context: str) -> str:
    # Port 1 on loopback refuses connections at once, so every query to this
    # context fails fast, the way it does for a cluster that has gone away.
    return yaml.safe_dump({
        'apiVersion': 'v1',
        'kind': 'Config',
        'clusters': [{
            'name': context,
            'cluster': {
                'server': 'https://127.0.0.1:1',
                'insecure-skip-tls-verify': True,
            },
        }],
        'users': [{
            'name': context,
            'user': {
                'token': 'e2e-not-a-token'
            }
        }],
        'contexts': [{
            'name': context,
            'context': {
                'cluster': context,
                'user': context
            },
        }],
        'current-context': context,
    })


def enable_kubernetes(workspace: str = 'default') -> None:
    """Record Kubernetes as enabled for ``workspace``, as a passing check
    would have."""
    global_user_state.set_enabled_clouds(['Kubernetes'],
                                         sky_cloud.CloudCapability.COMPUTE,
                                         workspace)


@pytest.fixture
def infra_context(uid: str) -> Iterator[str]:
    """One unreachable Kubernetes context, enabled for the default workspace.

    Server-wide state: exclusive tests only. Restores the kubeconfig and the
    cached check results afterwards, including any a Refresh wrote."""
    path = server_kubeconfig_path()
    with open(path, encoding='utf-8') as f:
        saved_kubeconfig = f.read()
    saved_rows = _check_rows()
    context = f'{uid}-ctx'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(_kubeconfig(context))
    enable_kubernetes('default')
    try:
        yield context
    finally:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(saved_kubeconfig)
        _restore_check_rows(saved_rows)


@pytest.fixture
def check_cache_restored() -> Iterator[None]:
    """Restore the cached check results a Refresh rewrites. Server-wide
    state: exclusive tests only."""
    saved_rows = _check_rows()
    try:
        yield
    finally:
        _restore_check_rows(saved_rows)


def _leftover_contexts() -> List[str]:
    try:
        with open(server_kubeconfig_path(), encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except (OSError, KeyError):
        return []
    return [
        f'kubeconfig context {c.get("name")}'
        for c in config.get('contexts') or []
        if seed.is_test_name(c.get('name'))
    ]


# --- workspaces --------------------------------------------------------------


def _api_result(admin_api: requests.Session, resp: requests.Response) -> Any:
    assert resp.status_code == 200, f'{resp.status_code} {resp.text}'
    rid = resp.headers['X-Skypilot-Request-ID']
    r = admin_api.get(
        f'{admin_api.base_url}/api/get?request_id={rid}',  # type: ignore[attr-defined]
        timeout=120)
    assert r.status_code == 200, f'{r.status_code} {r.text[:500]}'
    value = r.json().get('return_value')
    return json.loads(value) if value else None


@pytest.fixture
def make_workspace(admin_api: requests.Session,
                   uid: str) -> Iterator[Callable[[str], str]]:
    """Create a public workspace named ``<uid>-<label>`` through the API;
    returns its id. Deleted afterwards."""
    created: List[str] = []

    def make(label: str) -> str:
        name = f'{uid}-{label}'
        result = _api_result(
            admin_api,
            admin_api.post(
                f'{admin_api.base_url}/workspaces/create',  # type: ignore[attr-defined]
                json={
                    'workspace_name': name,
                    'config': {}
                },
                timeout=60))
        ids = [k for k, v in (result or {}).items() if v.get('name') == name]
        assert ids, f'creating workspace {name}: {result}'
        created.append(ids[0])
        return ids[0]

    yield make
    for ws_id in created:
        admin_api.post(
            f'{admin_api.base_url}/workspaces/delete',  # type: ignore[attr-defined]
            json={'workspace_name': ws_id},
            timeout=60)


def _leftover_workspaces() -> List[str]:
    try:
        with open(os.environ['SKYPILOT_GLOBAL_CONFIG'], encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except (OSError, KeyError):
        return []
    out = []
    for ws_id, ws in (config.get('workspaces') or {}).items():
        name = (ws or {}).get('name', ws_id) if isinstance(ws, dict) else ws_id
        if seed.is_test_name(name):
            out.append(f'workspace {name}')
    return out


seed.register_leftover_check(_leftover_volumes)
seed.register_leftover_check(_leftover_contexts)
seed.register_leftover_check(_leftover_workspaces)
