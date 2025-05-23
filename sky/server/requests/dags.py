"""Interface for accessing persisted DAGs"""

import dataclasses
import functools
import os
import pathlib
import time
import typing
from typing import Optional, Tuple
import uuid

from sky.server import constants as server_constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import dag_utils
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    from sky import dag as dag_lib


@dataclasses.dataclass
class StoredDAG:
    id: str
    dag_yaml: str
    config_yaml: str
    created_at: float


# Table in dags.db.
DAG_TABLE = 'dags'
DAG_COLUMNS = ['id', 'dag_yaml', 'config_yaml', 'created_at']

_DB_PATH = os.path.expanduser(server_constants.API_SERVER_DAG_DB_PATH)
pathlib.Path(_DB_PATH).parents[0].mkdir(parents=True, exist_ok=True)


def create_table(cursor, conn):
    """Create the DAGs table if it doesn't exist."""
    del conn
    cursor.execute(f"""\
        CREATE TABLE IF NOT EXISTS {DAG_TABLE} (
        id TEXT PRIMARY KEY,
        dag_yaml TEXT,
        config_yaml TEXT,
        created_at REAL)""")


_DB = None


def init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _DB
        if _DB is None:
            _DB = db_utils.SQLiteConn(_DB_PATH, create_table)
        return func(*args, **kwargs)

    return wrapper


@init_db
def get_dag(dag_id: str) -> Optional[StoredDAG]:
    """Get a DAG by ID.

    Args:
        dag_id: The ID of the DAG to retrieve.

    Returns:
        The DAG if found, None otherwise.
    """
    assert _DB is not None
    columns_str = ', '.join(DAG_COLUMNS)
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(
            f'SELECT {columns_str} FROM {DAG_TABLE} '
            'WHERE id LIKE ?', (dag_id + '%',))
        row = cursor.fetchone()
        if row is None:
            return None
    return StoredDAG(*row)


@init_db
def add_dag(dag: StoredDAG) -> None:
    """Add a DAG to the database.

    Args:
        dag: The DAG to add.
    """
    row = (dag.id, dag.dag_yaml, dag.config_yaml, dag.created_at)
    key_str = ', '.join(DAG_COLUMNS)
    fill_str = ', '.join(['?'] * len(row))
    assert _DB is not None
    with _DB.conn:
        cursor = _DB.conn.cursor()
        cursor.execute(
            f'INSERT OR REPLACE INTO {DAG_TABLE} ({key_str}) '
            f'VALUES ({fill_str})', row)


def save_dag_and_config(dag: 'dag_lib.Dag', config: config_utils.Config) -> str:
    """Save a DAG and config to the database and return the ID."""
    dag_yaml = dag_utils.dump_chain_dag_to_yaml_str(dag)
    config_yaml = common_utils.dump_yaml_str(dict(config))
    dag_id = str(uuid.uuid4())
    dag = StoredDAG(dag_id, dag_yaml, config_yaml, time.time())
    add_dag(dag)
    return dag_id


def load_dag_and_config(
        dag_id: str) -> Tuple['dag_lib.Dag', config_utils.Config]:
    """Load a DAG and config from the database."""
    stored_dag = get_dag(dag_id)
    if stored_dag is None:
        raise ValueError(f'DAG with ID {dag_id} not found')
    dag = dag_utils.load_chain_dag_from_yaml_str(stored_dag.dag_yaml)
    structs = common_utils.read_yaml_all_str(stored_dag.config_yaml)
    if structs:
        config = config_utils.Config.from_dict(structs[0])
    else:
        config = config_utils.Config()
    return dag, config
