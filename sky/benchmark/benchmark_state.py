"""Sky benchmark database, backed by sqlite."""
import enum
import os
import pathlib
import pickle
import sqlite3
import time
import threading
import typing
from typing import Any, Dict, List, Optional, Union
if typing.TYPE_CHECKING:
    from sky.backends import backend as backend_lib

_BENCHMARK_DB_PATH = os.path.expanduser('~/.sky/benchmark.db')
os.makedirs(pathlib.Path(_BENCHMARK_DB_PATH).parents[0], exist_ok=True)


class _BenchmarkSQLiteConn(threading.local):
    """Thread-local connection to the sqlite3 database.

    The database has two types of tables.
    1. Benchmark table stores the benchmark name and
        which resources are used for benchmarking.
    2. Benchmark Results table stores the benchmark results
        of the individual clusters used for benchmarking.
    """

    def __init__(self) -> None:
        super().__init__()
        self.conn = sqlite3.connect(_BENCHMARK_DB_PATH)
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self) -> None:
        # Table for Benchmark
        self.cursor.execute("""\
            CREATE TABLE IF NOT EXISTS benchmark (
            name TEXT PRIMARY KEY,
            task TEXT,
            launched_at INTEGER,
            status TEXT)""")
        # Table for Benchmark Results
        self.cursor.execute("""\
            CREATE TABLE IF NOT EXISTS benchmark_results (
            cluster TEXT PRIMARY KEY,
            num_nodes INTEGER,
            resources BLOB,
            start_ts INTEGER,
            first_ts INTEGER,
            last_ts INTEGER,
            iters INTEGER,
            benchmark TEXT,
            FOREIGN KEY (benchmark)
            REFERENCES benchmark (name)
                ON DELETE CASCADE
            )""")


_BENCHMARK_DB = _BenchmarkSQLiteConn()


class BenchmarkStatus(enum.Enum):
    """Benchmark status as recorded in table 'benchmark'."""

    RUNNING = 'RUNNING'
    FINISHED = 'FINISHED'


def add_benchmark(benchmark_name: str, task_name: Union[None, str]) -> None:
    """Add a new benchmark."""
    launched_at = int(time.time())
    if task_name is None:
        _BENCHMARK_DB.cursor.execute(
            'INSERT INTO benchmark'
            '(name, task, launched_at, status) '
            'VALUES (?, NULL, ?, ?, ?)', (benchmark_name, launched_at,
                                          BenchmarkStatus.RUNNING.value))
    else:
        _BENCHMARK_DB.cursor.execute(
            'INSERT INTO benchmark'
            '(name, task, launched_at, status) '
            'VALUES (?, ?, ?, ?, ?)', (benchmark_name, task_name, launched_at,
                                       BenchmarkStatus.RUNNING.value))
    _BENCHMARK_DB.conn.commit()


def add_benchmark_result(
        benchmark_name: str,
        cluster_handle: 'backend_lib.Backend.ResourceHandle') -> None:
    name = cluster_handle.cluster_name
    num_nodes = cluster_handle.launched_nodes
    resources = pickle.dumps(cluster_handle.launched_resources)
    _BENCHMARK_DB.cursor.execute(
        'INSERT INTO benchmark_results'
        '(cluster, num_nodes, resources, '
        'start_ts, first_ts, last_ts, iters, benchmark) '
        'VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?)',
        (name, num_nodes, resources, benchmark_name))
    _BENCHMARK_DB.conn.commit()


def update_benchmark_result(benchmark_name: str, cluster_name: str,
                            start_ts: int, first_ts: Union[None, int],
                            last_ts: int, iters: int) -> None:
    if first_ts is None:
        _BENCHMARK_DB.cursor.execute(
            'UPDATE benchmark_results SET '
            'start_ts=(?), first_ts=NULL, last_ts=(?), iters=(?) '
            'WHERE cluster=(?) AND benchmark=(?)',
            (start_ts, last_ts, iters, cluster_name, benchmark_name))
    else:
        _BENCHMARK_DB.cursor.execute(
            'UPDATE benchmark_results SET '
            'start_ts=(?), first_ts=(?), last_ts=(?), iters=(?) '
            'WHERE cluster=(?) AND benchmark=(?)',
            (start_ts, first_ts, last_ts, iters, cluster_name, benchmark_name))
    _BENCHMARK_DB.conn.commit()


def finish_benchmark(benchmark_name: str):
    """Mark a benchmark state as finished."""
    _BENCHMARK_DB.cursor.execute(
        'UPDATE benchmark SET status=(?) WHERE name=(?)',
        (BenchmarkStatus.FINISHED.value, benchmark_name))
    _BENCHMARK_DB.conn.commit()


def delete_benchmark(benchmark_name: str) -> None:
    """Delete a benchmark result."""
    _BENCHMARK_DB.cursor.execute(
        'DELETE FROM benchmark_results where benchmark=(?)', (benchmark_name,))
    _BENCHMARK_DB.cursor.execute('DELETE FROM benchmark WHERE name=(?)',
                                 (benchmark_name,))
    _BENCHMARK_DB.conn.commit()


def get_benchmark_from_name(benchmark_name: str) -> Optional[Dict[str, Any]]:
    """Get a benchmark from its name."""
    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT * FROM benchmark WHERE name=(?)', (benchmark_name,))
    for name, task, launched_at, status in rows:
        record = {
            'name': name,
            'task': task,
            'launched_at': launched_at,
            'status': BenchmarkStatus[status],
        }
        return record


def get_benchmarks() -> List[Dict[str, Any]]:
    """Get all benchmarks."""
    rows = _BENCHMARK_DB.cursor.execute('select * from benchmark')
    records = []
    for name, task, launched_at, status in rows:
        record = {
            'name': name,
            'task': task,
            'launched_at': launched_at,
            'status': BenchmarkStatus[status],
        }
        records.append(record)
    return records


def get_benchmark_results(benchmark_name: str) -> List[Dict[str, Any]]:
    rows = _BENCHMARK_DB.cursor.execute(
        'select * from benchmark_results where benchmark=(?)',
        (benchmark_name,))
    records = []
    for row in rows:
        record = {
            'cluster': row[0],
            'num_nodes': row[1],
            'resources': pickle.loads(row[2]),
            'start_ts': row[3],
            'first_ts': row[4],
            'last_ts': row[5],
            'iters': row[6],
            'benchmark': row[7],
        }
        records.append(record)
    return records
