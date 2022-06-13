"""Sky benchmark database, backed by sqlite."""
import enum
import os
import pathlib
import pickle
import sqlite3
import threading
import time
import typing
from typing import Any, Dict, List, NamedTuple, Optional
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
            bucket TEXT,
            launched_at INTEGER,
            status TEXT)""")
        # Table for Benchmark Results
        self.cursor.execute("""\
            CREATE TABLE IF NOT EXISTS benchmark_results (
            cluster TEXT PRIMARY KEY,
            num_nodes INTEGER,
            resources BLOB,
            record BLOB,
            benchmark TEXT,
            FOREIGN KEY (benchmark)
            REFERENCES benchmark (name)
                ON DELETE CASCADE
            )""")
        self.conn.commit()


_BENCHMARK_DB = _BenchmarkSQLiteConn()


class BenchmarkStatus(enum.Enum):
    """Benchmark status as recorded in table 'benchmark'."""

    RUNNING = 'RUNNING'
    FINISHED = 'FINISHED'


class BenchmarkRecord(NamedTuple):

    num_steps: int
    sec_per_step: float
    total_steps: Optional[int]
    start_ts: int
    first_ts: int
    last_ts: int


def add_benchmark(benchmark_name: str, task_name: Optional[str], bucket_name: str) -> None:
    """Add a new benchmark."""
    launched_at = int(time.time())
    if task_name is None:
        _BENCHMARK_DB.cursor.execute(
            'INSERT INTO benchmark'
            '(name, task, bucket, launched_at, status) '
            'VALUES (?, NULL, ?, ?, ?)',
            (benchmark_name, bucket_name, launched_at, BenchmarkStatus.RUNNING.value))
    else:
        _BENCHMARK_DB.cursor.execute(
            'INSERT INTO benchmark'
            '(name, task, bucket, launched_at, status) '
            'VALUES (?, ?, ?, ?, ?)', (benchmark_name, task_name, bucket_name, launched_at,
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
        '(cluster, num_nodes, resources, record, benchmark) '
        'VALUES (?, ?, ?, NULL, ?)',
        (name, num_nodes, resources, benchmark_name))
    _BENCHMARK_DB.conn.commit()


def update_benchmark_result(benchmark_name: str, cluster_name: str,
                            benchmark_record: BenchmarkRecord) -> None:
    _BENCHMARK_DB.cursor.execute(
        'UPDATE benchmark_results SET '
        'record=(?) WHERE benchmark=(?) AND cluster=(?)',
        (pickle.dumps(benchmark_record), benchmark_name, cluster_name))
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
    for name, task, bucket, launched_at, status in rows:
        record = {
            'name': name,
            'task': task,
            'bucket': bucket,
            'launched_at': launched_at,
            'status': BenchmarkStatus[status],
        }
        return record


def get_benchmarks() -> List[Dict[str, Any]]:
    """Get all benchmarks."""
    rows = _BENCHMARK_DB.cursor.execute('select * from benchmark')
    records = []
    for name, task, bucket, launched_at, status in rows:
        record = {
            'name': name,
            'task': task,
            'bucket': bucket,
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
    for cluster, num_nodes, resources, benchmark_record, benchmark in rows:
        if benchmark_record is not None:
            benchmark_record = pickle.loads(benchmark_record)
        record = {
            'cluster': cluster,
            'num_nodes': num_nodes,
            'resources': pickle.loads(resources),
            'record': benchmark_record,
            'benchmark': benchmark,
        }
        records.append(record)
    return records
