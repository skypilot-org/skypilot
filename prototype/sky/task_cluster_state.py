import os
import pathlib
import sqlite3
import time
import uuid

SESSION_DB_PATH = '/tmp/sky/session.db'


class TaskClusterState(object):
    """Manages and persists user session state."""

    def __init__(self):
        super().__init__()
        os.makedirs(pathlib.Path(SESSION_DB_PATH).parents[0], exist_ok=True)
        self.conn = sqlite3.connect(SESSION_DB_PATH)
        self.cursor = self.conn.cursor()
        self.init()

    def init(self):
        try:
            self.cursor.execute('select * from tasks limit 0')
            self.cursor.execute('select * from clusters limit 0')
        except sqlite3.OperationalError:
            # Tables do not exist, create them.
            self.cursor.execute("""CREATE TABLE tasks
                      (id TEXT PRIMARY KEY, name TEXT, launched_at INTEGER)""")
            self.cursor.execute("""CREATE TABLE clusters
                      (name TEXT PRIMARY KEY, lauched_at INTEGER, handle TEXT)"""
                               )

        self.conn.commit()

    def add_task(self, task):
        # TODO: move to Task object
        # TODO: add status attribute to Task object
        task_id = str(uuid.uuid4())
        task_name = task.name
        task_launched_at = int(time.time())

        self.cursor.execute(
            f"INSERT INTO tasks VALUES ('{task_id}','{task_name}',{task_launched_at})"
        )
        self.conn.commit()
        return task_id

    def remove_task(self, task_id):
        self.cursor.execute(f"DELETE FROM tasks WHERE id='{task_id}'")
        self.conn.commit()

    def add_cluster(self, cluster_name, cluster_handle):
        cluster_launched_at = int(time.time())
        self.cursor.execute(
            f"INSERT INTO clusters VALUES ('{cluster_name}',{cluster_launched_at},'{cluster_handle}')"
        )
        self.conn.commit()

    def remove_cluster(self, cluster_name):
        self.cursor.execute(f"DELETE FROM clusters WHERE name='{cluster_name}'")
        self.conn.commit()

    def get_handle_from_cluster_name(self, cluster_name):
        rows = self.cursor.execute(
            f"SELECT handle FROM clusters WHERE name='{cluster_name}'")
        for (handle,) in rows:
            return handle

    def get_cluster_name_from_handle(self, cluster_handle):
        rows = self.cursor.execute(
            f"SELECT name FROM clusters WHERE handle='{cluster_handle}'")
        for (name,) in rows:
            return name

    def get_tasks(self):
        rows = self.cursor.execute('select * from tasks')
        for id, name, launched_at in rows:
            yield {
                'id': id,
                'name': name,
                'launched_at': launched_at,
            }

    def get_clusters(self):
        rows = self.cursor.execute('select * from clusters')
        for name, launched_at, handle in rows:
            yield {'name': name, 'launched_at': launched_at, 'handle': handle}
