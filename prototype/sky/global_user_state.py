"""Sky global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for Sky to
  interact with a cluster.
"""
import enum
import os
import pathlib
import pickle
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

from sky import backends

_DB_PATH = os.path.expanduser('~/.sky/state.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

try:
    _CURSOR.execute('select * from clusters limit 0')
except sqlite3.OperationalError:
    # Tables do not exist, create them.
    _CURSOR.execute("""\
      CREATE TABLE clusters (
        name TEXT PRIMARY KEY,
        lauched_at INTEGER,
        handle BLOB,
        last_use TEXT,
        status TEXT)""")

_CONN.commit()

class ClusterStatus(enum.Enum):
    """Cluster status as recorded in table 'clusters'."""
    # NOTE: these statuses are as recorded in our local cache, the table
    # 'clusters'.  The actual cluster state may be different (e.g., an UP
    # cluster getting killed manually by the user or the cloud provider).

    # Initializing.  This means a backend.provision() call has started but has
    # not successfully finished. The cluster may be undergoing setup, may have
    # failed setup, may be live or down.
    INIT = 'INIT'

    # The cluster is recorded as up.  This means a backend.provision() has
    # previously succeeded.
    UP = 'UP'

    # Stopped.  This means a `sky stop` call has previously succeeded.
    STOPPED = 'STOPPED'


def _get_pretty_entry_point() -> str:
    """Returns the prettified entry point of this process (sys.argv).

    Example return values:

        $ sky run app.yaml  # 'sky run app.yaml'
        $ sky gpunode  # 'sky gpunode'
        $ python examples/app.py  # 'app.py'
    """
    argv = sys.argv
    basename = os.path.basename(argv[0])
    if basename == 'sky':
        # Turn '/.../anaconda/envs/py36/bin/sky' into 'sky', but keep other
        # things like 'examples/app.py'.
        argv[0] = basename
    return ' '.join(argv)


def add_or_update_cluster(cluster_name: str,
                          cluster_handle: backends.Backend.ResourceHandle,
                          ready: bool):
    """Adds or updates cluster_name -> cluster_handle mapping."""
    # FIXME: launched_at will be changed when the cluster is updated
    cluster_launched_at = int(time.time())
    handle = pickle.dumps(cluster_handle)
    last_use = _get_pretty_entry_point()
    status = ClusterStatus.UP if ready else ClusterStatus.INIT
    _CURSOR.execute(
        'INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?, ?)',
        (cluster_name, cluster_launched_at, handle, last_use, status.value))
    _CONN.commit()


def remove_cluster(cluster_name: str, terminate: bool):
    """Removes cluster_name mapping."""
    if terminate:
        _CURSOR.execute('DELETE FROM clusters WHERE name=(?)', (cluster_name,))
    else:
        handle = get_handle_from_cluster_name(cluster_name)
        if handle is None:
            return
        # Must invalidate head_ip: otherwise 'sky cpunode' on a stopped cpunode
        # will directly try to ssh, which leads to timeout.
        handle.head_ip = None
        _CURSOR.execute(
            'UPDATE clusters SET handle=(?), status=(?) WHERE name=(?)', (
                pickle.dumps(handle),
                ClusterStatus.STOPPED.value,
                cluster_name,
            ))
    _CONN.commit()


def get_handle_from_cluster_name(cluster_name: str
                                ) -> Optional[backends.Backend.ResourceHandle]:
    rows = _CURSOR.execute('SELECT handle FROM clusters WHERE name=(?)',
                           (cluster_name,))
    for (handle,) in rows:
        return pickle.loads(handle)


def get_cluster_name_from_handle(
        cluster_handle: backends.Backend.ResourceHandle,) -> Optional[str]:
    handle = pickle.dumps(cluster_handle)
    rows = _CURSOR.execute('SELECT name FROM clusters WHERE handle=(?)',
                           (handle,))
    for (name,) in rows:
        return name


def get_clusters() -> List[Dict[str, Any]]:
    rows = _CURSOR.execute('select * from clusters')
    records = []
    for name, launched_at, handle, last_use, status in rows:
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': ClusterStatus[status],
        })
    return records
