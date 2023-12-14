"""The database for services information."""
import collections
import enum
import os
import pathlib
import pickle
import sqlite3
import typing
from typing import Any, Dict, List, Optional, Union

import colorama
import psycopg2

from sky.serve import constants
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    import sky
    from sky.serve import replica_managers

# Retrieve environment variables to build the DB path
username = os.getenv('DB_USERNAME', 'postgres')
password = os.getenv('DB_PASSWORD', 'postgres')
host = os.getenv('DB_HOST', 'localhost')
port = os.getenv('DB_PORT', '5432')
database = os.getenv('DB_NAME', 'services')
_DB_PATH = f'postgres://{username}:{password}@{host}:{port}/{database}'
#PG connection:
pg = '?'


def create_table(cursor: Union[sqlite3.Cursor, psycopg2.extensions.cursor],
                 conn: Union[sqlite3.Connection,
                             psycopg2.extensions.connection], pgs: str) -> None:
    """Creates the service and replica tables if they do not exist."""

    cursor.execute(f"""\
        CREATE TABLE IF NOT EXISTS services (
        name TEXT PRIMARY KEY,
        controller_job_id INTEGER DEFAULT NULL,
        controller_port INTEGER DEFAULT NULL,
        load_balancer_port INTEGER DEFAULT NULL,
        status TEXT,
        uptime INTEGER DEFAULT NULL,
        policy TEXT DEFAULT NULL,
        auto_restart INTEGER DEFAULT NULL,
        requested_resources \
                   {'BYTEA' if pgs == '%s' else 'BLOB'} DEFAULT NULL)""")
    cursor.execute(f"""\
        CREATE TABLE IF NOT EXISTS replicas (
        service_name TEXT,
        replica_id INTEGER,
        replica_info {'BYTEA' if pgs == '%s' else 'BLOB'},
        PRIMARY KEY (service_name, replica_id))""")

    conn.commit()


#Try to connect to PG, if it fails, connect to sqlite
try:
    _DB = db_utils.DBConn(_DB_PATH, create_table)
    _DB.conn.autocommit = True  # type: ignore[union-attr]
    pg = '%s'
except psycopg2.Error:
    path: pathlib.Path = pathlib.Path(
        constants.SKYSERVE_METADATA_DIR) / 'services.db'
    path = path.expanduser().absolute()
    path.parents[0].mkdir(parents=True, exist_ok=True)
    _DB_PATH = str(path)
    _DB = db_utils.DBConn(_DB_PATH, create_table)

_UNIQUE_CONSTRAINT_FAILED_ERROR_MSG = 'UNIQUE constraint failed: services.name'


# === Statuses ===
class ReplicaStatus(enum.Enum):
    """Replica status."""

    # The `sky.launch` is pending due to max number of simultaneous launches.
    PENDING = 'PENDING'

    # The replica VM is being provisioned. i.e., the `sky.launch` is still
    # running.
    PROVISIONING = 'PROVISIONING'

    # The replica VM is provisioned and the service is starting. This indicates
    # user's `setup` section or `run` section is still running, and the
    # readiness probe fails.
    STARTING = 'STARTING'

    # The replica VM is provisioned and the service is ready, i.e. the
    # readiness probe is passed.
    READY = 'READY'

    # The service was ready before, but it becomes not ready now, i.e. the
    # readiness probe fails.
    NOT_READY = 'NOT_READY'

    # The replica VM is being shut down. i.e., the `sky down` is still running.
    SHUTTING_DOWN = 'SHUTTING_DOWN'

    # The replica VM is once failed and has been deleted.
    FAILED = 'FAILED'

    # `sky.down` failed during service teardown.
    # This could mean resource leakage.
    # TODO(tian): This status should be removed in the future, at which point
    # we should guarantee no resource leakage like regular sky.
    FAILED_CLEANUP = 'FAILED_CLEANUP'

    # The replica is a spot VM and it is preempted by the cloud provider.
    PREEMPTED = 'PREEMPTED'

    # Unknown. This should never happen (used only for unexpected errors).
    UNKNOWN = 'UNKNOWN'

    @classmethod
    def failed_statuses(cls) -> List['ReplicaStatus']:
        return [cls.FAILED, cls.FAILED_CLEANUP, cls.UNKNOWN]

    def colored_str(self) -> str:
        color = _REPLICA_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'


_REPLICA_STATUS_TO_COLOR = {
    ReplicaStatus.PENDING: colorama.Fore.YELLOW,
    ReplicaStatus.PROVISIONING: colorama.Fore.BLUE,
    ReplicaStatus.STARTING: colorama.Fore.CYAN,
    ReplicaStatus.READY: colorama.Fore.GREEN,
    ReplicaStatus.NOT_READY: colorama.Fore.YELLOW,
    ReplicaStatus.SHUTTING_DOWN: colorama.Fore.MAGENTA,
    ReplicaStatus.FAILED: colorama.Fore.RED,
    ReplicaStatus.FAILED_CLEANUP: colorama.Fore.RED,
    ReplicaStatus.PREEMPTED: colorama.Fore.MAGENTA,
    ReplicaStatus.UNKNOWN: colorama.Fore.RED,
}


class ServiceStatus(enum.Enum):
    """Service status as recorded in table 'services'."""

    # Controller is initializing
    CONTROLLER_INIT = 'CONTROLLER_INIT'

    # Replica is initializing and no failure
    REPLICA_INIT = 'REPLICA_INIT'

    # Controller failed to initialize / controller or load balancer process
    # status abnormal
    CONTROLLER_FAILED = 'CONTROLLER_FAILED'

    # At least one replica is ready
    READY = 'READY'

    # Service is being shutting down
    SHUTTING_DOWN = 'SHUTTING_DOWN'

    # At least one replica is failed and no replica is ready
    FAILED = 'FAILED'

    # Clean up failed
    FAILED_CLEANUP = 'FAILED_CLEANUP'

    @classmethod
    def failed_statuses(cls) -> List['ServiceStatus']:
        return [cls.CONTROLLER_FAILED, cls.FAILED_CLEANUP]

    @classmethod
    def refuse_to_terminate_statuses(cls) -> List['ServiceStatus']:
        return [cls.CONTROLLER_FAILED, cls.FAILED_CLEANUP, cls.SHUTTING_DOWN]

    def colored_str(self) -> str:
        color = _SERVICE_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def from_replica_statuses(
            cls, replica_statuses: List[ReplicaStatus]) -> 'ServiceStatus':
        status2num = collections.Counter(replica_statuses)
        # If one replica is READY, the service is READY.
        if status2num[ReplicaStatus.READY] > 0:
            return cls.READY
        if sum(status2num[status]
               for status in ReplicaStatus.failed_statuses()) > 0:
            return cls.FAILED
        return cls.REPLICA_INIT


_SERVICE_STATUS_TO_COLOR = {
    ServiceStatus.CONTROLLER_INIT: colorama.Fore.BLUE,
    ServiceStatus.REPLICA_INIT: colorama.Fore.BLUE,
    ServiceStatus.CONTROLLER_FAILED: colorama.Fore.RED,
    ServiceStatus.READY: colorama.Fore.GREEN,
    ServiceStatus.SHUTTING_DOWN: colorama.Fore.YELLOW,
    ServiceStatus.FAILED: colorama.Fore.RED,
    ServiceStatus.FAILED_CLEANUP: colorama.Fore.RED,
}


def add_service(name: str, controller_job_id: int, policy: str,
                auto_restart: bool, requested_resources: 'sky.Resources',
                status: ServiceStatus) -> bool:
    """Add a service in the database.

    Returns:
        True if the service is added successfully, False if the service already
        exists.
    """
    try:
        _DB.cursor.execute(
            f"""\
            INSERT INTO services
            (name, controller_job_id, status, policy,
            auto_restart, requested_resources)
            VALUES ({pg}, {pg}, {pg}, {pg}, {pg}, {pg})""",
            (name, controller_job_id, status.value, policy, int(auto_restart),
             pickle.dumps(requested_resources)))
        _DB.conn.commit()
    except sqlite3.IntegrityError as e:
        if str(e) != _UNIQUE_CONSTRAINT_FAILED_ERROR_MSG:
            raise RuntimeError('Unexpected database error') from e
        return False
    return True


def remove_service(service_name: str) -> None:
    """Removes a service from the database."""
    _DB.cursor.execute(f"""\
        DELETE FROM services WHERE name=({pg})""", (service_name,))
    _DB.conn.commit()


def set_service_uptime(service_name: str, uptime: int) -> None:
    """Sets the uptime of a service."""
    _DB.cursor.execute(
        f"""\
        UPDATE services SET
        uptime=({pg}) WHERE name=({pg})""", (uptime, service_name))
    _DB.conn.commit()


def set_service_status(service_name: str, status: ServiceStatus) -> None:
    """Sets the service status."""
    _DB.cursor.execute(
        f"""\
        UPDATE services SET
        status=({pg}) WHERE name=({pg})""", (status.value, service_name))
    _DB.conn.commit()


def set_service_controller_port(service_name: str,
                                controller_port: int) -> None:
    """Sets the controller port of a service."""
    _DB.cursor.execute(
        f"""\
        UPDATE services SET
        controller_port=({pg}) WHERE name=({pg})""",
        (controller_port, service_name))
    _DB.conn.commit()


def set_service_load_balancer_port(service_name: str,
                                   load_balancer_port: int) -> None:
    """Sets the load balancer port of a service."""
    _DB.cursor.execute(
        f"""\
        UPDATE services SET
        load_balancer_port=({pg}) WHERE name=({pg})""",
        (load_balancer_port, service_name))
    _DB.conn.commit()


def _get_service_from_row(row) -> Dict[str, Any]:
    (name, controller_job_id, controller_port, load_balancer_port, status,
     uptime, policy, auto_restart, requested_resources) = row[:9]
    return {
        'name': name,
        'controller_job_id': controller_job_id,
        'controller_port': controller_port,
        'load_balancer_port': load_balancer_port,
        'status': ServiceStatus[status],
        'uptime': uptime,
        'policy': policy,
        'auto_restart': bool(auto_restart),
        'requested_resources': pickle.loads(requested_resources)
                               if requested_resources is not None else None,
    }


def get_services() -> List[Dict[str, Any]]:
    """Get all existing service records."""
    _DB.cursor.execute('SELECT * FROM services')
    rows = _DB.cursor.fetchall()
    records = []
    for row in rows:
        records.append(_get_service_from_row(row))
    return records


def get_service_from_name(service_name: str) -> Optional[Dict[str, Any]]:
    """Get all existing service records."""
    _DB.cursor.execute(f'SELECT * FROM services WHERE name=({pg})',
                       (service_name,))
    rows = _DB.cursor.fetchall()
    for row in rows:
        return _get_service_from_row(row)
    return None


def get_glob_service_names(
        service_names: Optional[List[str]] = None) -> List[str]:
    """Get service names matching the glob patterns.

    Args:
        service_names: A list of glob patterns. If None, return all service
            names.

    Returns:
        A list of non-duplicated service names.
    """
    if service_names is None:
        _DB.cursor.execute('SELECT name FROM services')
        rows = _DB.cursor.fetchall()
    else:
        rows = []
        for service_name in service_names:
            _DB.cursor.execute(
                f'SELECT name FROM services WHERE name GLOB ({pg})',
                (service_name,))
            rows.extend(_DB.cursor.fetchall())
    return list({row[0] for row in rows})


# === Replica functions ===
def add_or_update_replica(service_name: str, replica_id: int,
                          replica_info: 'replica_managers.ReplicaInfo') -> None:
    """Adds or updates a replica in the database."""
    replica_info_serialized = pickle.dumps(replica_info)

    if pg == '%s':
        # PostgreSQL syntax using ON CONFLICT to update existing rows
        _DB.cursor.execute(
            """\
            INSERT INTO replicas (service_name, replica_id, replica_info)
            VALUES (%s, %s, %s)
            ON CONFLICT (service_name, replica_id)
            DO UPDATE SET replica_info = EXCLUDED.replica_info""",
            (service_name, replica_id, replica_info_serialized))
    else:
        # SQLite syntax using INSERT OR REPLACE
        _DB.cursor.execute(
            """\
            INSERT OR REPLACE INTO replicas
            (service_name, replica_id, replica_info)
            VALUES (?, ?, ?)""",
            (service_name, replica_id, replica_info_serialized))

    _DB.conn.commit()


def remove_replica(service_name: str, replica_id: int) -> None:
    """Removes a replica from the database."""
    _DB.cursor.execute(
        f"""\
        DELETE FROM replicas
        WHERE service_name=({pg})
        AND replica_id=({pg})""", (service_name, replica_id))
    _DB.conn.commit()


def get_replica_info_from_id(
        service_name: str,
        replica_id: int) -> Optional['replica_managers.ReplicaInfo']:
    """Gets a replica info from the database."""
    _DB.cursor.execute(
        f"""\
        SELECT replica_info FROM replicas
        WHERE service_name=({pg})
        AND replica_id=({pg})""", (service_name, replica_id))
    rows = _DB.cursor.fetchall()
    for row in rows:
        return pickle.loads(row[0])
    return None


def get_replica_infos(
        service_name: str) -> List['replica_managers.ReplicaInfo']:
    """Gets all replica infos of a service."""
    _DB.cursor.execute(
        f"""\
        SELECT replica_info FROM replicas
        WHERE service_name=({pg})""", (service_name,))
    rows = _DB.cursor.fetchall()
    return [pickle.loads(row[0]) for row in rows]


def total_number_provisioning_replicas() -> int:
    """Returns the total number of provisioning replicas."""
    _DB.cursor.execute('SELECT replica_info FROM replicas')
    rows = _DB.cursor.fetchall()
    provisioning_count = 0
    for row in rows:
        replica_info: 'replica_managers.ReplicaInfo' = pickle.loads(row[0])
        if replica_info.status == ReplicaStatus.PROVISIONING:
            provisioning_count += 1
    return provisioning_count
