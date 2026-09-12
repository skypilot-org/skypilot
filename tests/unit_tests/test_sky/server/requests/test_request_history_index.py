"""Cluster history reads must not scan unrelated finished requests."""
import sqlite3

import pytest

from sky.server.requests import requests


@pytest.mark.parametrize('upgrade', [False, True])
@pytest.mark.parametrize('cluster_name', ['target', 'missing'])
@pytest.mark.parametrize('user_id', [None, 'owner'])
def test_cluster_history_query_work_is_bounded(upgrade, cluster_name, user_id):
    with sqlite3.connect(':memory:') as conn:
        requests.create_table(conn.cursor(), conn)
        if upgrade:
            # Simulate the old schema, including its active-only cluster index.
            conn.execute('DROP INDEX IF EXISTS cluster_created_at_idx')
        conn.executemany(
            'INSERT INTO requests '
            '(request_id, name, status, created_at, cluster_name, user_id) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [(f'other-{i}', 'sky.launch', 'SUCCEEDED', i, 'other', 'owner')
             for i in range(10000)] +
            [
                ('old', 'sky.launch', 'SUCCEEDED', -2, 'target', 'owner'),
                ('new', 'sky.stop', 'SUCCEEDED', -1, 'target', 'owner'),
                ('active', 'sky.start', 'RUNNING', 10001, 'target', 'owner'),
                ('hidden', 'sky.status', 'SUCCEEDED', 10002, 'target', 'owner'),
                ('peer', 'sky.launch', 'SUCCEEDED', 10003, 'target', 'peer'),
            ])
        conn.commit()
        # Startup upgrades an existing populated database and is idempotent.
        requests.create_table(conn.cursor(), conn)
        requests.create_table(conn.cursor(), conn)
        query, params = requests.RequestTaskFilter(
            cluster_names=[cluster_name],
            user_id=user_id,
            exclude_request_names=['sky.status'],
            fields=['request_id'],
            sort=True,
            limit=100).build_query()
        steps = 0

        def limit_work():
            nonlocal steps
            steps += 1
            return steps >= 10

        # A full-history scan exceeds this deterministic instruction budget.
        conn.set_progress_handler(limit_work, 1000)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.set_progress_handler(None, 0)
        expected = []
        if cluster_name == 'target':
            expected = [('active',), ('new',), ('old',)]
            if user_id is None:
                expected.insert(0, ('peer',))
        assert rows == expected
