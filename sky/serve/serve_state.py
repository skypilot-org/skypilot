"""The database for services information."""
import collections
import enum
import pathlib
import pickle
import sqlite3
import typing
from typing import Any, Dict, List, Optional

import colorama

from sky.serve import constants
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    from sky.serve import infra_providers

_DB_PATH = pathlib.Path(constants.SERVE_PREFIX) / 'services.db'
_DB_PATH = _DB_PATH.expanduser().absolute()
_DB_PATH.parents[0].mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_DB_PATH)

# Module-level connection/cursor; thread-safe as the module is only imported
# once.
_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS services (
    name TEXT PRIMARY KEY,
    controller_job_id INTEGER,
    controller_port INTEGER,
    status TEXT,
    uptime INTEGER DEFAULT NULL)""")
_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS replicas (
    service_name TEXT,
    replica_id INTEGER,
    replica_info BLOB,
    PRIMARY KEY (service_name, replica_id))""")
_CONN.commit()


# === Statuses ===
class ReplicaStatus(enum.Enum):
    """Replica status."""

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

    # `sky.down` failed during service teardown. This could mean resource
    # leakage.
    FAILED_CLEANUP = 'FAILED_CLEANUP'

    # Unknown status. This should never happen.
    UNKNOWN = 'UNKNOWN'

    @classmethod
    def failed_statuses(cls):
        return [cls.FAILED, cls.FAILED_CLEANUP, cls.UNKNOWN]

    def colored_str(self):
        color = _REPLICA_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'


_REPLICA_STATUS_TO_COLOR = {
    ReplicaStatus.PROVISIONING: colorama.Fore.BLUE,
    ReplicaStatus.STARTING: colorama.Fore.CYAN,
    ReplicaStatus.READY: colorama.Fore.GREEN,
    ReplicaStatus.NOT_READY: colorama.Fore.YELLOW,
    ReplicaStatus.FAILED_CLEANUP: colorama.Fore.RED,
    ReplicaStatus.SHUTTING_DOWN: colorama.Fore.MAGENTA,
    ReplicaStatus.FAILED: colorama.Fore.RED,
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

    # Cannot connect to controller
    UNKNOWN = 'UNKNOWN'

    # At least one replica is failed and no replica is ready
    FAILED = 'FAILED'

    def colored_str(self):
        color = _SERVICE_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def from_replica_info(
            cls, replica_info: List[Dict[str, Any]]) -> 'ServiceStatus':
        status2num = collections.Counter([i['status'] for i in replica_info])
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
    ServiceStatus.UNKNOWN: colorama.Fore.YELLOW,
    ServiceStatus.FAILED: colorama.Fore.RED,
}


# === Service functions ===
def add_service(job_id: int, service_name: str, controller_port: int) -> None:
    """Adds a service to the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT INTO services
            (name, controller_job_id, controller_port, status)
            VALUES (?, ?, ?, ?)""", (service_name, job_id, controller_port,
                                     ServiceStatus.CONTROLLER_INIT.value))


def remove_service(service_name: str) -> None:
    """Removes a service from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute("""\
            DELETE FROM services WHERE name=(?)""", (service_name,))


def set_service_uptime(service_name: str, uptime: int) -> None:
    """Sets the uptime of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE services SET
            uptime=(?) WHERE name=(?)""", (uptime, service_name))


def set_service_status(service_name: str, status: ServiceStatus) -> None:
    """Sets the service status."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE services SET
            status=(?) WHERE name=(?)""", (status.value, service_name))


def _get_service_from_row(row) -> Dict[str, Any]:
    name, controller_job_id, controller_port, status, uptime = row[:5]
    return {
        'name': name,
        'controller_job_id': controller_job_id,
        'controller_port': controller_port,
        'status': ServiceStatus[status],
        'uptime': uptime,
    }


def get_services() -> List[Dict[str, Any]]:
    """Get all existing service records."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute('SELECT * FROM services').fetchall()
        records = []
        for row in rows:
            records.append(_get_service_from_row(row))
        return records


def get_service_from_name(service_name: str) -> Optional[Dict[str, Any]]:
    """Get all existing service records."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute('SELECT * FROM services WHERE name=(?)',
                              (service_name,)).fetchall()
        for row in rows:
            return _get_service_from_row(row)
        return None


# === Replica functions ===
def add_or_update_replica(service_name: str, replica_id: int,
                          replica_info: 'infra_providers.ReplicaInfo') -> None:
    """Adds a replica to the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT OR REPLACE INTO replicas
            (service_name, replica_id, replica_info)
            VALUES (?, ?, ?)""",
            (service_name, replica_id, pickle.dumps(replica_info)))


def remove_replica(service_name: str, replica_id: int) -> None:
    """Removes a replica from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            DELETE FROM replicas
            WHERE service_name=(?)
            AND replica_id=(?)""", (service_name, replica_id))


def get_replica_info_from_id(
        service_name: str,
        replica_id: int) -> Optional['infra_providers.ReplicaInfo']:
    """Gets a replica info from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            SELECT replica_info FROM replicas
            WHERE service_name=(?)
            AND replica_id=(?)""", (service_name, replica_id)).fetchall()
        for row in rows:
            return pickle.loads(row[0])
        return None


def get_replica_infos(service_name: str) -> List['infra_providers.ReplicaInfo']:
    """Gets all replica infos of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            SELECT replica_info FROM replicas
            WHERE service_name=(?)""", (service_name,)).fetchall()
        return [pickle.loads(row[0]) for row in rows]
