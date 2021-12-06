"""Sky global user state.

Examples/concepts:
    <task id>: Auto-generated unique ID used for tracking tasks.
    <task name>: User-supplied value for task, not necessarily unique.
    <cluster name>: User-supplied, unique name to identify a cluster.
    <cluster handle>: Automatically generated handle by Sky to interact with a
      cluster.
"""
import os
import pathlib
import pickle
import sqlite3
import time
from typing import Optional
import uuid

_DB_PATH = os.path.expanduser('~/.sky/state.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

try:
    _CURSOR.execute('select * from tasks limit 0')
    _CURSOR.execute('select * from clusters limit 0')
except sqlite3.OperationalError:
    # Tables do not exist, create them.
    _CURSOR.execute("""CREATE TABLE tasks
                (id TEXT PRIMARY KEY, name TEXT, launched_at INTEGER)""")
    _CURSOR.execute("""CREATE TABLE clusters
                (name TEXT PRIMARY KEY, lauched_at INTEGER, handle BLOB)""")
_CONN.commit()


def add_task(task):
    task_id = str(uuid.uuid4())[:6]  # TODO: make ids more pleasant
    task_name = task.name
    task_launched_at = int(time.time())

    _CURSOR.execute('INSERT INTO tasks VALUES (?,?,?)',
                    (task_id, task_name, task_launched_at))
    _CONN.commit()
    return task_id


def remove_task(task_id):
    _CURSOR.execute('DELETE FROM tasks WHERE id=(?)', (task_id,))
    _CONN.commit()


def add_or_update_cluster(cluster_name, cluster_handle):
    """Adds or updates cluster_name -> cluster_handle mapping."""
    cluster_launched_at = int(time.time())
    handle = pickle.dumps(cluster_handle)
    _CURSOR.execute('INSERT OR REPLACE INTO clusters VALUES (?, ?, ?)',
                    (cluster_name, cluster_launched_at, handle))
    _CONN.commit()


def remove_cluster(cluster_name):
    """Removes cluster_name mapping."""
    _CURSOR.execute('DELETE FROM clusters WHERE name=(?)', (cluster_name,))
    _CONN.commit()


def get_handle_from_cluster_name(cluster_name) -> Optional[str]:
    rows = _CURSOR.execute('SELECT handle FROM clusters WHERE name=(?)',
                           (cluster_name,))
    for (handle,) in rows:
        return pickle.loads(handle)


def get_cluster_name_from_handle(cluster_handle):
    handle = pickle.dumps(cluster_handle)
    rows = _CURSOR.execute('SELECT name FROM clusters WHERE handle=(?)',
                           (handle,))
    for (name,) in rows:
        return name


def get_tasks():
    rows = _CURSOR.execute('select * from tasks')
    records = []
    for task_id, name, launched_at in rows:
        records.append({
            'id': task_id,
            'name': name,
            'launched_at': launched_at,
        })
    return records


def get_clusters():
    rows = _CURSOR.execute('select * from clusters')
    records = []
    for name, launched_at, handle in rows:
        records.append({
            'name': name,
            'launched_at': launched_at,
            'handle': pickle.loads(handle),
        })
    return records
