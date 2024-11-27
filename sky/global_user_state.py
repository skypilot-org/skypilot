"""Global user state, backed by a sqlite database.

Concepts:
- Cluster name: a user-supplied or auto-generated unique name to identify a
  cluster.
- Cluster handle: (non-user facing) an opaque backend handle for us to
  interact with a cluster.
"""
import json
import os
import pathlib
import pickle
import sqlite3
import time
import typing
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from sky import clouds
from sky import status_lib
from sky.utils import common_utils
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky.data import Storage

_ENABLED_CLOUDS_KEY = 'enabled_clouds'

_DB_PATH = os.path.expanduser('~/.sky/state.db')
pathlib.Path(_DB_PATH).parents[0].mkdir(parents=True, exist_ok=True)


def create_table(cursor, conn):
    # Enable WAL mode to avoid locking issues.
    # See: issue #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if not common_utils.is_wsl():
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    # Table for Clusters
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS clusters (
        name TEXT PRIMARY KEY,
        launched_at INTEGER,
        handle BLOB,
        last_use TEXT,
        status TEXT,
        autostop INTEGER DEFAULT -1,
        metadata TEXT DEFAULT "{}",
        to_down INTEGER DEFAULT 0,
        owner TEXT DEFAULT null,
        cluster_hash TEXT DEFAULT null,
        storage_mounts_metadata BLOB DEFAULT null,
        cluster_ever_up INTEGER DEFAULT 0,
        status_updated_at INTEGER DEFAULT null)""")

    # Table for Cluster History
    # usage_intervals: List[Tuple[int, int]]
    #  Specifies start and end timestamps of cluster.
    #  When the last end time is None, the cluster is still UP.
    #  Example: [(start1, end1), (start2, end2), (start3, None)]

    # requested_resources: Set[resource_lib.Resource]
    #  Requested resources fetched from task that user specifies.

    # launched_resources: Optional[resources_lib.Resources]
    #  Actual launched resources fetched from handle for cluster.

    # num_nodes: Optional[int] number of nodes launched.

    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS cluster_history (
        cluster_hash TEXT PRIMARY KEY,
        name TEXT,
        num_nodes int,
        requested_resources BLOB,
        launched_resources BLOB,
        usage_intervals BLOB)""")
    # Table for configs (e.g. enabled clouds)
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)""")
    # Table for Storage
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS storage (
        name TEXT PRIMARY KEY,
        launched_at INTEGER,
        handle BLOB,
        last_use TEXT,
        status TEXT)""")
    # For backward compatibility.
    # TODO(zhwu): Remove this function after all users have migrated to
    # the latest version of SkyPilot.
    # Add autostop column to clusters table
    db_utils.add_column_to_table(cursor, conn, 'clusters', 'autostop',
                                 'INTEGER DEFAULT -1')

    db_utils.add_column_to_table(cursor, conn, 'clusters', 'metadata',
                                 'TEXT DEFAULT "{}"')

    db_utils.add_column_to_table(cursor, conn, 'clusters', 'to_down',
                                 'INTEGER DEFAULT 0')

    db_utils.add_column_to_table(cursor, conn, 'clusters', 'owner', 'TEXT')

    db_utils.add_column_to_table(cursor, conn, 'clusters', 'cluster_hash',
                                 'TEXT DEFAULT null')

    db_utils.add_column_to_table(cursor, conn, 'clusters',
                                 'storage_mounts_metadata', 'BLOB DEFAULT null')
    db_utils.add_column_to_table(
        cursor,
        conn,
        'clusters',
        'cluster_ever_up',
        'INTEGER DEFAULT 0',
        # Set the value to 1 so that all the existing clusters before #2977
        # are considered as ever up, i.e:
        #   existing cluster's default (null) -> 1;
        #   new cluster's default -> 0;
        # This is conservative for the existing clusters: even if some INIT
        # clusters were never really UP, setting it to 1 means they won't be
        # auto-deleted during any failover.
        value_to_replace_existing_entries=1)

    db_utils.add_column_to_table(cursor, conn, 'clusters', 'status_updated_at',
                                 'INTEGER DEFAULT null')

    conn.commit()


_DB = db_utils.SQLiteConn(_DB_PATH, create_table)


def add_or_update_cluster(cluster_name: str,
                          cluster_handle: 'backends.ResourceHandle',
                          requested_resources: Optional[Set[Any]],
                          ready: bool,
                          is_launch: bool = True):
    """Adds or updates cluster_name -> cluster_handle mapping.

    Args:
        cluster_name: Name of the cluster.
        cluster_handle: backends.ResourceHandle of the cluster.
        requested_resources: Resources requested for cluster.
        ready: Whether the cluster is ready to use. If False, the cluster will
            be marked as INIT, otherwise it will be marked as UP.
        is_launch: if the cluster is firstly launched. If True, the launched_at
            and last_use will be updated. Otherwise, use the old value.
    """
    # FIXME: launched_at will be changed when `sky launch -c` is called.
    handle = pickle.dumps(cluster_handle)
    cluster_launched_at = int(time.time()) if is_launch else None
    last_use = common_utils.get_pretty_entry_point() if is_launch else None
    status = status_lib.ClusterStatus.INIT
    if ready:
        status = status_lib.ClusterStatus.UP
    status_updated_at = int(time.time())

    # TODO (sumanth): Cluster history table will have multiple entries
    # when the cluster failover through multiple regions (one entry per region).
    # It can be more inaccurate for the multi-node cluster
    # as the failover can have the nodes partially UP.
    cluster_hash = _get_hash_for_existing_cluster(cluster_name) or str(
        uuid.uuid4())
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    # first time a cluster is being launched
    if not usage_intervals:
        usage_intervals = []

    # if this is the cluster init or we are starting after a stop
    if not usage_intervals or usage_intervals[-1][-1] is not None:
        if cluster_launched_at is None:
            # This could happen when the cluster is restarted manually on the
            # cloud console. In this case, we will use the current time as the
            # cluster launched time.
            # TODO(zhwu): We should use the time when the cluster is restarted
            # to be more accurate.
            cluster_launched_at = int(time.time())
        usage_intervals.append((cluster_launched_at, None))

    _DB.cursor.execute(
        'INSERT or REPLACE INTO clusters'
        # All the fields need to exist here, even if they don't need
        # be changed, as the INSERT OR REPLACE statement will replace
        # the field of the existing row with the default value if not
        # specified.
        '(name, launched_at, handle, last_use, status, '
        'autostop, to_down, metadata, owner, cluster_hash, '
        'storage_mounts_metadata, cluster_ever_up, status_updated_at) '
        'VALUES ('
        # name
        '?, '
        # launched_at
        'COALESCE('
        '?, (SELECT launched_at FROM clusters WHERE name=?)), '
        # handle
        '?, '
        # last_use
        'COALESCE('
        '?, (SELECT last_use FROM clusters WHERE name=?)), '
        # status
        '?, '
        # autostop
        # Keep the old autostop value if it exists, otherwise set it to
        # default -1.
        'COALESCE('
        '(SELECT autostop FROM clusters WHERE name=? AND status!=?), -1), '
        # Keep the old to_down value if it exists, otherwise set it to
        # default 0.
        'COALESCE('
        '(SELECT to_down FROM clusters WHERE name=? AND status!=?), 0),'
        # Keep the old metadata value if it exists, otherwise set it to
        # default {}.
        'COALESCE('
        '(SELECT metadata FROM clusters WHERE name=?), "{}"),'
        # Keep the old owner value if it exists, otherwise set it to
        # default null.
        'COALESCE('
        '(SELECT owner FROM clusters WHERE name=?), null),'
        # cluster_hash
        '?,'
        # storage_mounts_metadata
        'COALESCE('
        '(SELECT storage_mounts_metadata FROM clusters WHERE name=?), null), '
        # cluster_ever_up
        '((SELECT cluster_ever_up FROM clusters WHERE name=?) OR ?),'
        # status_updated_at
        '?'
        ')',
        (
            # name
            cluster_name,
            # launched_at
            cluster_launched_at,
            cluster_name,
            # handle
            handle,
            # last_use
            last_use,
            cluster_name,
            # status
            status.value,
            # autostop
            cluster_name,
            status_lib.ClusterStatus.STOPPED.value,
            # to_down
            cluster_name,
            status_lib.ClusterStatus.STOPPED.value,
            # metadata
            cluster_name,
            # owner
            cluster_name,
            # cluster_hash
            cluster_hash,
            # storage_mounts_metadata
            cluster_name,
            # cluster_ever_up
            cluster_name,
            int(ready),
            # status_updated_at
            status_updated_at,
        ))

    launched_nodes = getattr(cluster_handle, 'launched_nodes', None)
    launched_resources = getattr(cluster_handle, 'launched_resources', None)
    _DB.cursor.execute(
        'INSERT or REPLACE INTO cluster_history'
        '(cluster_hash, name, num_nodes, requested_resources, '
        'launched_resources, usage_intervals) '
        'VALUES ('
        # hash
        '?, '
        # name
        '?, '
        # requested resources
        '?, '
        # launched resources
        '?, '
        # number of nodes
        '?, '
        # usage intervals
        '?)',
        (
            # hash
            cluster_hash,
            # name
            cluster_name,
            # number of nodes
            launched_nodes,
            # requested resources
            pickle.dumps(requested_resources),
            # launched resources
            pickle.dumps(launched_resources),
            # usage intervals
            pickle.dumps(usage_intervals),
        ))

    _DB.conn.commit()


def update_last_use(cluster_name: str):
    """Updates the last used command for the cluster."""
    _DB.cursor.execute('UPDATE clusters SET last_use=(?) WHERE name=(?)',
                       (common_utils.get_pretty_entry_point(), cluster_name))
    _DB.conn.commit()


def remove_cluster(cluster_name: str, terminate: bool) -> None:
    """Removes cluster_name mapping."""
    cluster_hash = _get_hash_for_existing_cluster(cluster_name)
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    # usage_intervals is not None and not empty
    if usage_intervals:
        assert cluster_hash is not None, cluster_name
        start_time = usage_intervals.pop()[0]
        end_time = int(time.time())
        usage_intervals.append((start_time, end_time))
        _set_cluster_usage_intervals(cluster_hash, usage_intervals)

    if terminate:
        _DB.cursor.execute('DELETE FROM clusters WHERE name=(?)',
                           (cluster_name,))
    else:
        handle = get_handle_from_cluster_name(cluster_name)
        if handle is None:
            return
        # Must invalidate IP list to avoid directly trying to ssh into a
        # stopped VM, which leads to timeout.
        if hasattr(handle, 'stable_internal_external_ips'):
            handle.stable_internal_external_ips = None
        current_time = int(time.time())
        _DB.cursor.execute(
            'UPDATE clusters SET handle=(?), status=(?), '
            'status_updated_at=(?) WHERE name=(?)', (
                pickle.dumps(handle),
                status_lib.ClusterStatus.STOPPED.value,
                current_time,
                cluster_name,
            ))
    _DB.conn.commit()


def get_handle_from_cluster_name(
        cluster_name: str) -> Optional['backends.ResourceHandle']:
    assert cluster_name is not None, 'cluster_name cannot be None'
    rows = _DB.cursor.execute('SELECT handle FROM clusters WHERE name=(?)',
                              (cluster_name,))
    for (handle,) in rows:
        return pickle.loads(handle)
    return None


def get_glob_cluster_names(cluster_name: str) -> List[str]:
    assert cluster_name is not None, 'cluster_name cannot be None'
    rows = _DB.cursor.execute('SELECT name FROM clusters WHERE name GLOB (?)',
                              (cluster_name,))
    return [row[0] for row in rows]


def set_cluster_status(cluster_name: str,
                       status: status_lib.ClusterStatus) -> None:
    current_time = int(time.time())
    _DB.cursor.execute(
        'UPDATE clusters SET status=(?), status_updated_at=(?) WHERE name=(?)',
        (status.value, current_time, cluster_name))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def set_cluster_autostop_value(cluster_name: str, idle_minutes: int,
                               to_down: bool) -> None:
    _DB.cursor.execute(
        'UPDATE clusters SET autostop=(?), to_down=(?) WHERE name=(?)', (
            idle_minutes,
            int(to_down),
            cluster_name,
        ))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def get_cluster_launch_time(cluster_name: str) -> Optional[int]:
    rows = _DB.cursor.execute('SELECT launched_at FROM clusters WHERE name=(?)',
                              (cluster_name,))
    for (launch_time,) in rows:
        if launch_time is None:
            return None
        return int(launch_time)
    return None


def get_cluster_info(cluster_name: str) -> Optional[Dict[str, Any]]:
    rows = _DB.cursor.execute('SELECT metadata FROM clusters WHERE name=(?)',
                              (cluster_name,))
    for (metadata,) in rows:
        if metadata is None:
            return None
        return json.loads(metadata)
    return None


def set_cluster_info(cluster_name: str, metadata: Dict[str, Any]) -> None:
    _DB.cursor.execute('UPDATE clusters SET metadata=(?) WHERE name=(?)', (
        json.dumps(metadata),
        cluster_name,
    ))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def get_cluster_storage_mounts_metadata(
        cluster_name: str) -> Optional[Dict[str, Any]]:
    rows = _DB.cursor.execute(
        'SELECT storage_mounts_metadata FROM clusters WHERE name=(?)',
        (cluster_name,))
    for (storage_mounts_metadata,) in rows:
        if storage_mounts_metadata is None:
            return None
        return pickle.loads(storage_mounts_metadata)
    return None


def set_cluster_storage_mounts_metadata(
        cluster_name: str, storage_mounts_metadata: Dict[str, Any]) -> None:
    _DB.cursor.execute(
        'UPDATE clusters SET storage_mounts_metadata=(?) WHERE name=(?)', (
            pickle.dumps(storage_mounts_metadata),
            cluster_name,
        ))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def _get_cluster_usage_intervals(
        cluster_hash: Optional[str]
) -> Optional[List[Tuple[int, Optional[int]]]]:
    if cluster_hash is None:
        return None
    rows = _DB.cursor.execute(
        'SELECT usage_intervals FROM cluster_history WHERE cluster_hash=(?)',
        (cluster_hash,))
    for (usage_intervals,) in rows:
        if usage_intervals is None:
            return None
        return pickle.loads(usage_intervals)
    return None


def _get_cluster_launch_time(cluster_hash: str) -> Optional[int]:
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)
    if usage_intervals is None:
        return None
    return usage_intervals[0][0]


def _get_cluster_duration(cluster_hash: str) -> int:
    total_duration = 0
    usage_intervals = _get_cluster_usage_intervals(cluster_hash)

    if usage_intervals is None:
        return total_duration

    for i, (start_time, end_time) in enumerate(usage_intervals):
        # duration from latest start time to time of query
        if start_time is None:
            continue
        if end_time is None:
            assert i == len(usage_intervals) - 1, i
            end_time = int(time.time())
        start_time, end_time = int(start_time), int(end_time)
        total_duration += end_time - start_time
    return total_duration


def _set_cluster_usage_intervals(
        cluster_hash: str, usage_intervals: List[Tuple[int,
                                                       Optional[int]]]) -> None:
    _DB.cursor.execute(
        'UPDATE cluster_history SET usage_intervals=(?) WHERE cluster_hash=(?)',
        (
            pickle.dumps(usage_intervals),
            cluster_hash,
        ))

    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster hash {cluster_hash} not found.')


def set_owner_identity_for_cluster(cluster_name: str,
                                   owner_identity: Optional[List[str]]) -> None:
    if owner_identity is None:
        return
    owner_identity_str = json.dumps(owner_identity)
    _DB.cursor.execute('UPDATE clusters SET owner=(?) WHERE name=(?)',
                       (owner_identity_str, cluster_name))

    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Cluster {cluster_name} not found.')


def _get_hash_for_existing_cluster(cluster_name: str) -> Optional[str]:
    rows = _DB.cursor.execute(
        'SELECT cluster_hash FROM clusters WHERE name=(?)', (cluster_name,))
    for (cluster_hash,) in rows:
        if cluster_hash is None:
            return None
        return cluster_hash
    return None


def get_launched_resources_from_cluster_hash(
        cluster_hash: str) -> Optional[Tuple[int, Any]]:

    rows = _DB.cursor.execute(
        'SELECT num_nodes, launched_resources '
        'FROM cluster_history WHERE cluster_hash=(?)', (cluster_hash,))
    for (num_nodes, launched_resources) in rows:
        if num_nodes is None or launched_resources is None:
            return None
        launched_resources = pickle.loads(launched_resources)
        return num_nodes, launched_resources
    return None


def _load_owner(record_owner: Optional[str]) -> Optional[List[str]]:
    if record_owner is None:
        return None
    try:
        result = json.loads(record_owner)
        if result is not None and not isinstance(result, list):
            # Backwards compatibility for old records, which were stored as
            # a string instead of a list. It is possible that json.loads
            # will parse the string with all numbers as an int or escape
            # some characters, such as \n, so we need to use the original
            # record_owner.
            return [record_owner]
        return result
    except json.JSONDecodeError:
        # Backwards compatibility for old records, which were stored as
        # a string instead of a list. This will happen when the previous
        # UserId is a string instead of an int.
        return [record_owner]


def _load_storage_mounts_metadata(
    record_storage_mounts_metadata: Optional[bytes]
) -> Optional[Dict[str, 'Storage.StorageMetadata']]:
    if not record_storage_mounts_metadata:
        return None
    return pickle.loads(record_storage_mounts_metadata)


def get_cluster_from_name(
        cluster_name: Optional[str]) -> Optional[Dict[str, Any]]:
    rows = _DB.cursor.execute(
        'SELECT name, launched_at, handle, last_use, status, autostop, '
        'metadata, to_down, owner, cluster_hash, storage_mounts_metadata, '
        'cluster_ever_up, status_updated_at FROM clusters WHERE name=(?)',
        (cluster_name,)).fetchall()
    for row in rows:
        # Explicitly specify the number of fields to unpack, so that
        # we can add new fields to the database in the future without
        # breaking the previous code.
        (name, launched_at, handle, last_use, status, autostop, metadata,
         to_down, owner, cluster_hash, storage_mounts_metadata, cluster_ever_up,
         status_updated_at) = row[:13]
        # TODO: use namedtuple instead of dict
        record = {
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': status_lib.ClusterStatus[status],
            'autostop': autostop,
            'to_down': bool(to_down),
            'owner': _load_owner(owner),
            'metadata': json.loads(metadata),
            'cluster_hash': cluster_hash,
            'storage_mounts_metadata':
                _load_storage_mounts_metadata(storage_mounts_metadata),
            'cluster_ever_up': bool(cluster_ever_up),
            'status_updated_at': status_updated_at,
        }
        return record
    return None


def get_clusters() -> List[Dict[str, Any]]:
    rows = _DB.cursor.execute(
        'select name, launched_at, handle, last_use, status, autostop, '
        'metadata, to_down, owner, cluster_hash, storage_mounts_metadata, '
        'cluster_ever_up, status_updated_at from clusters '
        'order by launched_at desc').fetchall()
    records = []
    for row in rows:
        (name, launched_at, handle, last_use, status, autostop, metadata,
         to_down, owner, cluster_hash, storage_mounts_metadata, cluster_ever_up,
         status_updated_at) = row[:13]
        # TODO: use namedtuple instead of dict
        record = {
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': status_lib.ClusterStatus[status],
            'autostop': autostop,
            'to_down': bool(to_down),
            'owner': _load_owner(owner),
            'metadata': json.loads(metadata),
            'cluster_hash': cluster_hash,
            'storage_mounts_metadata':
                _load_storage_mounts_metadata(storage_mounts_metadata),
            'cluster_ever_up': bool(cluster_ever_up),
            'status_updated_at': status_updated_at,
        }

        records.append(record)
    return records


def get_clusters_from_history() -> List[Dict[str, Any]]:
    rows = _DB.cursor.execute(
        'SELECT ch.cluster_hash, ch.name, ch.num_nodes, '
        'ch.launched_resources, ch.usage_intervals, clusters.status  '
        'FROM cluster_history ch '
        'LEFT OUTER JOIN clusters '
        'ON ch.cluster_hash=clusters.cluster_hash ').fetchall()

    # '(cluster_hash, name, num_nodes, requested_resources, '
    #         'launched_resources, usage_intervals) '
    records = []

    for row in rows:
        # TODO: use namedtuple instead of dict

        (
            cluster_hash,
            name,
            num_nodes,
            launched_resources,
            usage_intervals,
            status,
        ) = row[:6]

        if status is not None:
            status = status_lib.ClusterStatus[status]

        record = {
            'name': name,
            'launched_at': _get_cluster_launch_time(cluster_hash),
            'duration': _get_cluster_duration(cluster_hash),
            'num_nodes': num_nodes,
            'resources': pickle.loads(launched_resources),
            'cluster_hash': cluster_hash,
            'usage_intervals': pickle.loads(usage_intervals),
            'status': status,
        }

        records.append(record)

    # sort by launch time, descending in recency
    records = sorted(records, key=lambda record: -record['launched_at'])
    return records


def get_cluster_names_start_with(starts_with: str) -> List[str]:
    rows = _DB.cursor.execute('SELECT name FROM clusters WHERE name LIKE (?)',
                              (f'{starts_with}%',))
    return [row[0] for row in rows]


def get_cached_enabled_clouds() -> List[clouds.Cloud]:
    rows = _DB.cursor.execute('SELECT value FROM config WHERE key = ?',
                              (_ENABLED_CLOUDS_KEY,))
    ret = []
    for (value,) in rows:
        ret = json.loads(value)
        break
    enabled_clouds: List[clouds.Cloud] = []
    for c in ret:
        try:
            cloud = clouds.CLOUD_REGISTRY.from_str(c)
        except ValueError:
            # Handle the case for the clouds whose support has been removed from
            # SkyPilot, e.g., 'local' was a cloud in the past and may be stored
            # in the database for users before #3037. We should ignore removed
            # clouds and continue.
            continue
        if cloud is not None:
            enabled_clouds.append(cloud)
    return enabled_clouds


def set_enabled_clouds(enabled_clouds: List[str]) -> None:
    _DB.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                       (_ENABLED_CLOUDS_KEY, json.dumps(enabled_clouds)))
    _DB.conn.commit()


def add_or_update_storage(storage_name: str,
                          storage_handle: 'Storage.StorageMetadata',
                          storage_status: status_lib.StorageStatus):
    storage_launched_at = int(time.time())
    handle = pickle.dumps(storage_handle)
    last_use = common_utils.get_pretty_entry_point()

    def status_check(status):
        return status in status_lib.StorageStatus

    if not status_check(storage_status):
        raise ValueError(f'Error in updating global state. Storage Status '
                         f'{storage_status} is passed in incorrectly')
    _DB.cursor.execute('INSERT OR REPLACE INTO storage VALUES (?, ?, ?, ?, ?)',
                       (storage_name, storage_launched_at, handle, last_use,
                        storage_status.value))
    _DB.conn.commit()


def remove_storage(storage_name: str):
    """Removes Storage from Database"""
    _DB.cursor.execute('DELETE FROM storage WHERE name=(?)', (storage_name,))
    _DB.conn.commit()


def set_storage_status(storage_name: str,
                       status: status_lib.StorageStatus) -> None:
    _DB.cursor.execute('UPDATE storage SET status=(?) WHERE name=(?)', (
        status.value,
        storage_name,
    ))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage {storage_name} not found.')


def get_storage_status(storage_name: str) -> Optional[status_lib.StorageStatus]:
    assert storage_name is not None, 'storage_name cannot be None'
    rows = _DB.cursor.execute('SELECT status FROM storage WHERE name=(?)',
                              (storage_name,))
    for (status,) in rows:
        return status_lib.StorageStatus[status]
    return None


def set_storage_handle(storage_name: str,
                       handle: 'Storage.StorageMetadata') -> None:
    _DB.cursor.execute('UPDATE storage SET handle=(?) WHERE name=(?)', (
        pickle.dumps(handle),
        storage_name,
    ))
    count = _DB.cursor.rowcount
    _DB.conn.commit()
    assert count <= 1, count
    if count == 0:
        raise ValueError(f'Storage{storage_name} not found.')


def get_handle_from_storage_name(
        storage_name: Optional[str]) -> Optional['Storage.StorageMetadata']:
    if storage_name is None:
        return None
    rows = _DB.cursor.execute('SELECT handle FROM storage WHERE name=(?)',
                              (storage_name,))
    for (handle,) in rows:
        if handle is None:
            return None
        return pickle.loads(handle)
    return None


def get_glob_storage_name(storage_name: str) -> List[str]:
    assert storage_name is not None, 'storage_name cannot be None'
    rows = _DB.cursor.execute('SELECT name FROM storage WHERE name GLOB (?)',
                              (storage_name,))
    return [row[0] for row in rows]


def get_storage_names_start_with(starts_with: str) -> List[str]:
    rows = _DB.cursor.execute('SELECT name FROM storage WHERE name LIKE (?)',
                              (f'{starts_with}%',))
    return [row[0] for row in rows]


def get_storage() -> List[Dict[str, Any]]:
    rows = _DB.cursor.execute('select * from storage')
    records = []
    for name, launched_at, handle, last_use, status in rows:
        # TODO: use namedtuple instead of dict
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
            'last_use': last_use,
            'status': status_lib.StorageStatus[status],
        })
    return records
