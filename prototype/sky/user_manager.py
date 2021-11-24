import sqlite3
import time
import uuid


class UserManager(object):
    """Manages and persists user state."""

    def __init__(self):
        self.conn = sqlite3.connect('session.db')
        self.cursor = self.conn.cursor()
        self.init()

    def init(self):
        try:
            self.cursor.execute('select * from tasks limit 0')
            self.cursor.execute('select * from clusters limit 0')
        except sqlite3.OperationalError:
            # Tables do not exist, create them.
            self.cursor.execute('''CREATE TABLE tasks
                      (id TEXT PRIMARY KEY, name TEXT, launched_at INTEGER, status TEXT)'''
                               )
            self.cursor.execute('''CREATE TABLE clusters
                      (id TEXT PRIMARY KEY, cloud TEXT, nodes INTEGER, status TEXT)'''
                               )

        self.conn.commit()

    def add_task(self, task):
        task_id = str(uuid.uuid4())[:6]  # TODO: move to Task object
        # TODO: add status attribute to Task object

        self.cursor.execute(
            f"INSERT INTO tasks VALUES ('{task_id}','{task.name}',{time.time()},'Started')"
        )
        self.conn.commit()

    def add_cluster(self, cluster):
        pass

    def get_tasks(self):
        rows = self.cursor.execute('select * from tasks')
        for id, name, launched_at, status in rows:
            yield {
                'id': id,
                'name': name,
                'launched_at': launched_at,
                'status': status
            }

    def get_clusters(self):
        rows = self.cursor.execute('select * from clusters')
        for id, name, launched_at, status in rows:
            yield {
                'id': id,
                'name': name,
                'launched_at': launched_at,
                'status': status
            }
