"""Sky global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for Sky to
  interact with a cluster.
"""
import json
import os
import pathlib
import pickle
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

from sky import backends
from sky import clouds

_ENABLED_CLOUDS_KEY = 'enabled_clouds'

_DB_PATH = os.path.expanduser('~/.sky/state.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

try:
    _CURSOR.execute('select * from clusters limit 0')
    _CURSOR.execute('select * from config limit 0')
except sqlite3.OperationalError:
    # Tables do not exist, create them.
    _CURSOR.execute("""\
      CREATE TABLE clusters (
        name TEXT PRIMARY KEY,
        lauched_at INTEGER,
        handle BLOB,
        last_use TEXT)""")
    _CURSOR.execute("""\
        CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)""")
_CONN.commit()


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
                          cluster_handle: backends.Backend.ResourceHandle):
    """Adds or updates cluster_name -> cluster_handle mapping."""
    cluster_launched_at = int(time.time())
    handle = pickle.dumps(cluster_handle)
    last_use = _get_pretty_entry_point()
    _CURSOR.execute('INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?)',
                    (cluster_name, cluster_launched_at, handle, last_use))
    _CONN.commit()


def remove_cluster(cluster_name: str):
    """Removes cluster_name mapping."""
    _CURSOR.execute('DELETE FROM clusters WHERE name=(?)', (cluster_name,))
    _CONN.commit()


def get_handle_from_cluster_name(cluster_name: str) -> Optional[str]:
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
    for name, launched_at, handle, last_use in rows:
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
        })
    return records


def get_enabled_clouds() -> List[str]:
    rows = _CURSOR.execute('SELECT value FROM config WHERE key = ?',
                           (_ENABLED_CLOUDS_KEY,))
    ret = []
    for (value,) in rows:
        ret = json.loads(value)
        break
    return [clouds.cloud_factory(cloud)() for cloud in ret]


def set_enabled_clouds(enabled_clouds: List[str]) -> None:
    _CURSOR.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                    (_ENABLED_CLOUDS_KEY, json.dumps(enabled_clouds)))
    _CONN.commit()
