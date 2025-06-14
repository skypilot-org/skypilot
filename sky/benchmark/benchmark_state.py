"""Sky benchmark database, backed by sqlite."""
import enum
import functools
import os
import pathlib
import pickle
import sqlite3
import threading
import time
import typing
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

if typing.TYPE_CHECKING:
    from sky.backends import backend as backend_lib

_BENCHMARK_BUCKET_NAME_KEY = 'bucket_name'
_BENCHMARK_BUCKET_TYPE_KEY = 'bucket_type'

_BENCHMARK_DB_PATH = os.path.expanduser('~/.sky/benchmark.db')


class _BenchmarkSQLiteConn(threading.local):
    """Thread-local connection to the sqlite3 database.

    The database has three types of tables.
    1. Benchmark table stores the benchmark names and
        which resources are used for benchmarking.
    2. Benchmark Config table stores Sky Benchmark configurations
        (e.g., benchmark bucket name).
    3. Benchmark Results table stores the benchmark results
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
            launched_at INTEGER)""")
        # Table for Benchmark Config (e.g., benchmark bucket name)
        self.cursor.execute("""\
            CREATE TABLE IF NOT EXISTS benchmark_config (
            key TEXT PRIMARY KEY, value TEXT)""")
        # Table for Benchmark Results
        self.cursor.execute("""\
            CREATE TABLE IF NOT EXISTS benchmark_results (
            cluster TEXT PRIMARY KEY,
            status TEXT,
            num_nodes INTEGER,
            resources BLOB,
            record BLOB,
            benchmark TEXT,
            FOREIGN KEY (benchmark)
            REFERENCES benchmark (name)
                ON DELETE CASCADE
            )""")
        self.conn.commit()


_BENCHMARK_DB = None
_benchmark_db_init_lock = threading.Lock()


def _init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _BENCHMARK_DB
        if _BENCHMARK_DB:
            return func(*args, **kwargs)
        with _benchmark_db_init_lock:
            if not _BENCHMARK_DB:
                os.makedirs(pathlib.Path(_BENCHMARK_DB_PATH).parents[0],
                            exist_ok=True)
                _BENCHMARK_DB = _BenchmarkSQLiteConn()
        return func(*args, **kwargs)

    return wrapper


class BenchmarkStatus(enum.Enum):
    """Benchmark job status.

    This is slightly different from the job status maintained by the job queue
    in the following aspects:
    1. THE INIT state includes both INIT and PENDING states, because
        the benchmarking job is always the first job of the cluster.
    2. The TERMINATED state includes the CANCELLED and FAILED states, as we
        cannot distinguish the two states when the cluster is not alive.
    3. The SUCCEEDED state is renamed to FINISHED.
    """
    # Corresponding job status: INIT, PENDING.
    INIT = 'INIT'

    # Corresponding job status: RUNNING.
    RUNNING = 'RUNNING'

    # Job status: CANCELLED, FAILED.
    # TODO(woosuk): Add KILLED state to distinguish whether the benchmarking
    # job is killed by the user or by its own error.
    TERMINATED = 'TERMINATED'

    # Job status: SUCCEEDED.
    # Jobs terminated with zero exit code.
    FINISHED = 'FINISHED'

    @classmethod
    def terminal_statuses(cls) -> List['BenchmarkStatus']:
        return (cls.TERMINATED, cls.FINISHED)

    def is_terminal(self) -> bool:
        return self in self.terminal_statuses()


class BenchmarkRecord(NamedTuple):
    """Benchmark record."""

    # The time when the benchmarking job is launched.
    start_time: Optional[float] = None

    # The last known time. Either the job finish time or the last step time.
    last_time: Optional[float] = None

    # The number of steps taken so far.
    num_steps_so_far: Optional[int] = None

    # The average time (in secs) taken per step.
    seconds_per_step: Optional[float] = None

    # The estimated end-to-end time (in secs) of the benchmarking job.
    estimated_total_seconds: Optional[float] = None


@_init_db
def add_benchmark(benchmark_name: str, task_name: Optional[str],
                  bucket_name: str) -> None:
    """Add a new benchmark."""
    assert _BENCHMARK_DB is not None
    launched_at = int(time.time())
    _BENCHMARK_DB.cursor.execute(
        'INSERT INTO benchmark'
        '(name, task, bucket, launched_at) '
        'VALUES (?, ?, ?, ?)',
        (benchmark_name, task_name, bucket_name, launched_at))
    _BENCHMARK_DB.conn.commit()


@_init_db
def add_benchmark_result(benchmark_name: str,
                         cluster_handle: 'backend_lib.ResourceHandle') -> None:
    assert _BENCHMARK_DB is not None
    name = cluster_handle.cluster_name
    num_nodes = cluster_handle.launched_nodes
    resources = pickle.dumps(cluster_handle.launched_resources)
    _BENCHMARK_DB.cursor.execute(
        'INSERT INTO benchmark_results'
        '(cluster, status, num_nodes, resources, record, benchmark) '
        'VALUES (?, ?, ?, ?, NULL, ?)', (name, BenchmarkStatus.INIT.value,
                                         num_nodes, resources, benchmark_name))
    _BENCHMARK_DB.conn.commit()


@_init_db
def update_benchmark_result(
        benchmark_name: str, cluster_name: str,
        benchmark_status: BenchmarkStatus,
        benchmark_record: Optional[BenchmarkRecord]) -> None:
    assert _BENCHMARK_DB is not None
    _BENCHMARK_DB.cursor.execute(
        'UPDATE benchmark_results SET '
        'status=(?), record=(?) WHERE benchmark=(?) AND cluster=(?)',
        (benchmark_status.value, pickle.dumps(benchmark_record), benchmark_name,
         cluster_name))
    _BENCHMARK_DB.conn.commit()


@_init_db
def delete_benchmark(benchmark_name: str) -> None:
    """Delete a benchmark result."""
    assert _BENCHMARK_DB is not None
    _BENCHMARK_DB.cursor.execute(
        'DELETE FROM benchmark_results WHERE benchmark=(?)', (benchmark_name,))
    _BENCHMARK_DB.cursor.execute('DELETE FROM benchmark WHERE name=(?)',
                                 (benchmark_name,))
    _BENCHMARK_DB.conn.commit()


@_init_db
def get_benchmark_from_name(benchmark_name: str) -> Optional[Dict[str, Any]]:
    """Get a benchmark from its name."""
    assert _BENCHMARK_DB is not None
    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT * FROM benchmark WHERE name=(?)', (benchmark_name,))
    for name, task, bucket, launched_at in rows:
        record = {
            'name': name,
            'task': task,
            'bucket': bucket,
            'launched_at': launched_at,
        }
        return record


@_init_db
def get_benchmarks() -> List[Dict[str, Any]]:
    """Get all benchmarks."""
    assert _BENCHMARK_DB is not None
    rows = _BENCHMARK_DB.cursor.execute('SELECT * FROM benchmark')
    records = []
    for name, task, bucket, launched_at in rows:
        record = {
            'name': name,
            'task': task,
            'bucket': bucket,
            'launched_at': launched_at,
        }
        records.append(record)
    return records


@_init_db
def set_benchmark_bucket(bucket_name: str, bucket_type: str) -> None:
    """Save the benchmark bucket name and type."""
    assert _BENCHMARK_DB is not None
    _BENCHMARK_DB.cursor.execute(
        'REPLACE INTO benchmark_config (key, value) VALUES (?, ?)',
        (_BENCHMARK_BUCKET_NAME_KEY, bucket_name))
    _BENCHMARK_DB.cursor.execute(
        'REPLACE INTO benchmark_config (key, value) VALUES (?, ?)',
        (_BENCHMARK_BUCKET_TYPE_KEY, bucket_type))
    _BENCHMARK_DB.conn.commit()


@_init_db
def get_benchmark_bucket() -> Tuple[Optional[str], Optional[str]]:
    """Get the benchmark bucket name and type."""
    assert _BENCHMARK_DB is not None
    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT value FROM benchmark_config WHERE key=(?)',
        (_BENCHMARK_BUCKET_NAME_KEY,))
    bucket_name = None
    for (value,) in rows:
        bucket_name = value
        break

    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT value FROM benchmark_config WHERE key=(?)',
        (_BENCHMARK_BUCKET_TYPE_KEY,))
    bucket_type = None
    for (value,) in rows:
        bucket_type = value
        break
    return bucket_name, bucket_type


@_init_db
def get_benchmark_clusters(benchmark_name: str) -> List[str]:
    """Get all clusters for a benchmark."""
    assert _BENCHMARK_DB is not None
    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT cluster FROM benchmark_results WHERE benchmark=(?)',
        (benchmark_name,))
    return [row[0] for row in rows]


@_init_db
def get_benchmark_results(benchmark_name: str) -> List[Dict[str, Any]]:
    assert _BENCHMARK_DB is not None
    rows = _BENCHMARK_DB.cursor.execute(
        'SELECT * FROM benchmark_results WHERE benchmark=(?)',
        (benchmark_name,))
    records = []
    for (cluster, status, num_nodes, resources, benchmark_record,
         benchmark) in rows:
        if benchmark_record is not None:
            benchmark_record = pickle.loads(benchmark_record)
        record = {
            'cluster': cluster,
            'status': BenchmarkStatus[status],
            'num_nodes': num_nodes,
            'resources': pickle.loads(resources),
            'record': benchmark_record,
            'benchmark': benchmark,
        }
        records.append(record)
    return records
