"""Sky global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for Sky to
  interact with a cluster.
"""
import enum
import json
import os
import pathlib
import pickle
import sqlite3
import sys
import time
import typing
from typing import Any, Dict, List, Optional

from sky import clouds

if typing.TYPE_CHECKING:
    from sky import backends
    from sky.data import Storage

_ENABLED_CLOUDS_KEY = 'enabled_clouds'

_DB_PATH = os.path.expanduser('~/.sky/state.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

# Table for Clusters
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS clusters (
    name TEXT PRIMARY KEY,
    lauched_at INTEGER,
    handle BLOB,
    last_use TEXT,
    status TEXT)""")
# Table for Sky Config (e.g. enabled clouds)
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY, value TEXT)""")
# Table for Storage
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS storage (
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


class StorageStatus(enum.Enum):
    """Storage status as recorded in table 'storage'."""

    # Initializing Storage Class
    INIT = 'INIT'

    # Uploading to AWS
    UPLOAD_AWS = 'UPLOAD_AWS'

    # Uploading to GCP
    UPLOAD_GCP = 'UPLOAD_GCP'

    # Uploading to AWS
    UPLOAD_AZURE = 'UPLOAD_AZURE'

    # Failed to Upload to Cloud
    UPLOAD_FAIL = 'UPLOAD_FAIL'

    # Finished uploading, in terminal state
    DONE = 'DONE'


def _get_pretty_entry_point() -> str:
    """Returns the prettified entry point of this process (sys.argv).

    Example return values:

        $ sky launch app.yaml  # 'sky launch app.yaml'
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
                          cluster_handle: 'backends.Backend.ResourceHandle',
                          ready: bool):
    """Adds or updates cluster_name -> cluster_handle mapping."""
    # FIXME: launched_at will be changed when `sky launch -c` is called.
    cluster_launched_at = int(time.time())
    handle = pickle.dumps(cluster_handle)
    last_use = _get_pretty_entry_point()
    status = ClusterStatus.UP if ready else ClusterStatus.INIT
    _CURSOR.execute(
        'INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?, ?)',
        (cluster_name, cluster_launched_at, handle, last_use, status.value))
    _CONN.commit()


def update_last_use(cluster_name: str):
    """Updates the last used command for the cluster."""
    _CURSOR.execute('UPDATE clusters SET last_use=(?) WHERE name=(?)',
                    (_get_pretty_entry_point(), cluster_name))
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


def get_handle_from_cluster_name(
        cluster_name: str) -> Optional['backends.Backend.ResourceHandle']:
    assert cluster_name is not None, 'cluster_name cannot be None'
    rows = _CURSOR.execute('SELECT handle FROM clusters WHERE name=(?)',
                           (cluster_name,))
    for (handle,) in rows:
        return pickle.loads(handle)


def get_glob_cluster_names(cluster_name: str) -> List[str]:
    assert cluster_name is not None, 'cluster_name cannot be None'
    rows = _CURSOR.execute('SELECT name FROM clusters WHERE name GLOB (?)',
                           (cluster_name,))
    return [row[0] for row in rows]


def get_cluster_name_from_handle(
        cluster_handle: 'backends.Backend.ResourceHandle') -> Optional[str]:
    handle = pickle.dumps(cluster_handle)
    rows = _CURSOR.execute('SELECT name FROM clusters WHERE handle=(?)',
                           (handle,))
    for (name,) in rows:
        return name


def get_status_from_cluster_name(
        cluster_name: Optional[str]) -> Optional[ClusterStatus]:
    rows = _CURSOR.execute('SELECT status FROM clusters WHERE name=(?)',
                           (cluster_name,))
    for (status,) in rows:
        return ClusterStatus[status]


def set_cluster_status(cluster_name: str, status: ClusterStatus) -> None:
    _CURSOR.execute('UPDATE clusters SET status=(?) WHERE name=(?)', (
        status.value,
        cluster_name,
    ))
    count = _CURSOR.rowcount
    _CONN.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def get_clusters() -> List[Dict[str, Any]]:
    rows = _CURSOR.execute('select * from clusters')
    records = []
    for name, launched_at, handle, last_use, status in rows:
        # TODO: use namedtuple instead of dict
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': ClusterStatus[status],
        })
    return records


def get_enabled_clouds() -> List[clouds.Cloud]:
    rows = _CURSOR.execute('SELECT value FROM config WHERE key = ?',
                           (_ENABLED_CLOUDS_KEY,))
    ret = []
    for (value,) in rows:
        ret = json.loads(value)
        break
    return [clouds.from_str(cloud) for cloud in ret]


def set_enabled_clouds(enabled_clouds: List[str]) -> None:
    _CURSOR.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                    (_ENABLED_CLOUDS_KEY, json.dumps(enabled_clouds)))
    _CONN.commit()


def add_or_update_storage(storage_name: str,
                          storage_handle: 'Storage.StorageMetadata',
                          storage_status: StorageStatus):
    storage_launched_at = int(time.time())
    handle = pickle.dumps(storage_handle)
    last_use = _get_pretty_entry_point()

    def status_check(status):
        return status in StorageStatus

    if not status_check(storage_status):
        raise ValueError(f'Error in updating global state. Storage Status '
                         f'{storage_status} is passed in incorrectly')
    _CURSOR.execute('INSERT OR REPLACE INTO storage VALUES (?, ?, ?, ?, ?)',
                    (storage_name, storage_launched_at, handle, last_use,
                     storage_status.value))
    _CONN.commit()


def remove_storage(storage_name: str):
    """Removes Storage from Database"""
    _CURSOR.execute('DELETE FROM storage WHERE name=(?)', (storage_name,))
    _CONN.commit()


def set_storage_status(storage_name: str, status: StorageStatus) -> None:
    _CURSOR.execute('UPDATE storage SET status=(?) WHERE name=(?)', (
        status.value,
        storage_name,
    ))
    count = _CURSOR.rowcount
    _CONN.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage{storage_name} not found.')


def get_storage_status(storage_name: str) -> None:
    assert storage_name is not None, 'storage_name cannot be None'
    rows = _CURSOR.execute('SELECT status FROM storage WHERE name=(?)',
                           (storage_name,))
    for (status,) in rows:
        return StorageStatus[status]


def set_storage_handle(storage_name: str, handle: 'Storage.StorageMetadata'):
    _CURSOR.execute('UPDATE storage SET handle=(?) WHERE name=(?)', (
        pickle.dumps(handle),
        storage_name,
    ))
    count = _CURSOR.rowcount
    _CONN.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage{storage_name} not found.')


def get_handle_from_storage_name(storage_name: str):
    assert storage_name is not None, 'storage_name cannot be None'
    rows = _CURSOR.execute('SELECT handle FROM storage WHERE name=(?)',
                           (storage_name,))
    for (handle,) in rows:
        return pickle.loads(handle)


def get_storage() -> List[Dict[str, Any]]:
    rows = _CURSOR.execute('select * from storage')
    records = []
    for name, launched_at, handle, last_use, status in rows:
        # TODO: use namedtuple instead of dict
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': StorageStatus[status],
        })
    return records
