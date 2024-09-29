"""The database for services information."""
import collections
import enum
import json
import pathlib
import pickle
import sqlite3
import typing
from typing import Any, Dict, List, Optional, Tuple

import colorama

from sky.serve import constants
from sky.utils import db_utils

if typing.TYPE_CHECKING:
    from sky.serve import replica_managers
    from sky.serve import service_spec

_DB_PATH = pathlib.Path(constants.SKYSERVE_METADATA_DIR) / 'services.db'
_DB_PATH = _DB_PATH.expanduser().absolute()
_DB_PATH.parents[0].mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_DB_PATH)


def create_table(cursor: 'sqlite3.Cursor', conn: 'sqlite3.Connection') -> None:
    """Creates the service and replica tables if they do not exist."""

    # auto_restart column is deprecated.
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS services (
        name TEXT PRIMARY KEY,
        controller_job_id INTEGER DEFAULT NULL,
        controller_port INTEGER DEFAULT NULL,
        load_balancer_port INTEGER DEFAULT NULL,
        status TEXT,
        uptime INTEGER DEFAULT NULL,
        policy TEXT DEFAULT NULL,
        auto_restart INTEGER DEFAULT NULL,
        requested_resources BLOB DEFAULT NULL)""")
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS replicas (
        service_name TEXT,
        replica_id INTEGER,
        replica_info BLOB,
        PRIMARY KEY (service_name, replica_id))""")
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS version_specs (
        version INTEGER, 
        service_name TEXT,
        spec BLOB,
        PRIMARY KEY (service_name, version))""")
    conn.commit()


_DB = db_utils.SQLiteConn(_DB_PATH, create_table)
# Backward compatibility.
db_utils.add_column_to_table(_DB.cursor, _DB.conn, 'services',
                             'requested_resources_str', 'TEXT')
# Deprecated: switched to `active_versions` below for the version considered
# active by the load balancer. The authscaler/replica_manager version can be
# found in the version_specs table.
db_utils.add_column_to_table(_DB.cursor, _DB.conn, 'services',
                             'current_version',
                             f'INTEGER DEFAULT {constants.INITIAL_VERSION}')
# The versions that is activated for the service. This is a list of integers in
# json format.
db_utils.add_column_to_table(_DB.cursor, _DB.conn, 'services',
                             'active_versions',
                             f'TEXT DEFAULT {json.dumps([])!r}')
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

    # The replica fails due to user's run/setup.
    FAILED = 'FAILED'

    # The replica fails due to initial delay exceeded.
    FAILED_INITIAL_DELAY = 'FAILED_INITIAL_DELAY'

    # The replica fails due to healthiness check.
    FAILED_PROBING = 'FAILED_PROBING'

    # The replica fails during launching
    FAILED_PROVISION = 'FAILED_PROVISION'

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
        return [
            cls.FAILED, cls.FAILED_CLEANUP, cls.FAILED_INITIAL_DELAY,
            cls.FAILED_PROBING, cls.FAILED_PROVISION, cls.UNKNOWN
        ]

    @classmethod
    def terminal_statuses(cls) -> List['ReplicaStatus']:
        return [cls.SHUTTING_DOWN, cls.PREEMPTED, cls.UNKNOWN
               ] + cls.failed_statuses()

    @classmethod
    def scale_down_decision_order(cls) -> List['ReplicaStatus']:
        # Scale down replicas in the order of replica initialization
        return [
            cls.PENDING, cls.PROVISIONING, cls.STARTING, cls.NOT_READY,
            cls.READY
        ]

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
    ReplicaStatus.FAILED_INITIAL_DELAY: colorama.Fore.RED,
    ReplicaStatus.FAILED_PROBING: colorama.Fore.RED,
    ReplicaStatus.FAILED_PROVISION: colorama.Fore.RED,
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

    # No replica
    NO_REPLICA = 'NO_REPLICA'

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
        # When min_replicas = 0, there is no (provisioning) replica.
        if len(replica_statuses) == 0:
            return cls.NO_REPLICA
        return cls.REPLICA_INIT


_SERVICE_STATUS_TO_COLOR = {
    ServiceStatus.CONTROLLER_INIT: colorama.Fore.BLUE,
    ServiceStatus.REPLICA_INIT: colorama.Fore.BLUE,
    ServiceStatus.CONTROLLER_FAILED: colorama.Fore.RED,
    ServiceStatus.READY: colorama.Fore.GREEN,
    ServiceStatus.SHUTTING_DOWN: colorama.Fore.YELLOW,
    ServiceStatus.FAILED: colorama.Fore.RED,
    ServiceStatus.FAILED_CLEANUP: colorama.Fore.RED,
    ServiceStatus.NO_REPLICA: colorama.Fore.MAGENTA,
}


def add_service(name: str, controller_job_id: int, policy: str,
                requested_resources_str: str, status: ServiceStatus) -> bool:
    """Add a service in the database.

    Returns:
        True if the service is added successfully, False if the service already
        exists.
    """
    try:
        with db_utils.safe_cursor(_DB_PATH) as cursor:
            cursor.execute(
                """\
                INSERT INTO services
                (name, controller_job_id, status, policy,
                requested_resources_str)
                VALUES (?, ?, ?, ?, ?)""",
                (name, controller_job_id, status.value, policy,
                 requested_resources_str))

    except sqlite3.IntegrityError as e:
        if str(e) != _UNIQUE_CONSTRAINT_FAILED_ERROR_MSG:
            raise RuntimeError('Unexpected database error') from e
        return False
    return True


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


def set_service_status_and_active_versions(
        service_name: str,
        status: ServiceStatus,
        active_versions: Optional[List[int]] = None) -> None:
    """Sets the service status."""
    vars_to_set = 'status=(?)'
    values: Tuple[str, ...] = (status.value, service_name)
    if active_versions is not None:
        vars_to_set = 'status=(?), active_versions=(?)'
        values = (status.value, json.dumps(active_versions), service_name)
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            f"""\
            UPDATE services SET
            {vars_to_set} WHERE name=(?)""", values)


def set_service_controller_port(service_name: str,
                                controller_port: int) -> None:
    """Sets the controller port of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE services SET
            controller_port=(?) WHERE name=(?)""",
            (controller_port, service_name))


def set_service_load_balancer_port(service_name: str,
                                   load_balancer_port: int) -> None:
    """Sets the load balancer port of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE services SET
            load_balancer_port=(?) WHERE name=(?)""",
            (load_balancer_port, service_name))


def _get_service_from_row(row) -> Dict[str, Any]:
    (current_version, name, controller_job_id, controller_port,
     load_balancer_port, status, uptime, policy, _, requested_resources,
     requested_resources_str, _, active_versions) = row[:13]
    return {
        'name': name,
        'controller_job_id': controller_job_id,
        'controller_port': controller_port,
        'load_balancer_port': load_balancer_port,
        'status': ServiceStatus[status],
        'uptime': uptime,
        'policy': policy,
        # The version of the autoscaler/replica manager are on. It can be larger
        # than the active versions as the load balancer may not consider the
        # latest version to be active for serving traffic.
        'version': current_version,
        # The versions that is active for the load balancer. This is a list of
        # integers in json format. This is mainly for display purpose.
        'active_versions': json.loads(active_versions),
        # TODO(tian): Backward compatibility.
        # Remove after 2 minor release, 0.6.0.
        'requested_resources': pickle.loads(requested_resources)
                               if requested_resources is not None else None,
        'requested_resources_str': requested_resources_str,
    }


def get_services() -> List[Dict[str, Any]]:
    """Get all existing service records."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute('SELECT v.max_version, s.* FROM services s '
                              'JOIN ('
                              'SELECT service_name, MAX(version) as max_version'
                              ' FROM version_specs GROUP BY service_name) v '
                              'ON s.name=v.service_name').fetchall()
    records = []
    for row in rows:
        records.append(_get_service_from_row(row))
    return records


def get_service_from_name(service_name: str) -> Optional[Dict[str, Any]]:
    """Get all existing service records."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            'SELECT v.max_version, s.* FROM services s '
            'JOIN ('
            'SELECT service_name, MAX(version) as max_version '
            'FROM version_specs WHERE service_name=(?)) v '
            'ON s.name=v.service_name WHERE name=(?)',
            (service_name, service_name)).fetchall()
    for row in rows:
        return _get_service_from_row(row)
    return None


def get_service_versions(service_name: str) -> List[int]:
    """Gets all versions of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            SELECT DISTINCT version FROM version_specs
            WHERE service_name=(?)""", (service_name,)).fetchall()
    return [row[0] for row in rows]


def get_glob_service_names(
        service_names: Optional[List[str]] = None) -> List[str]:
    """Get service names matching the glob patterns.

    Args:
        service_names: A list of glob patterns. If None, return all service
            names.

    Returns:
        A list of non-duplicated service names.
    """
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        if service_names is None:
            rows = cursor.execute('SELECT name FROM services').fetchall()
        else:
            rows = []
            for service_name in service_names:
                rows.extend(
                    cursor.execute(
                        'SELECT name FROM services WHERE name GLOB (?)',
                        (service_name,)).fetchall())
    return list({row[0] for row in rows})


# === Replica functions ===
def add_or_update_replica(service_name: str, replica_id: int,
                          replica_info: 'replica_managers.ReplicaInfo') -> None:
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
        replica_id: int) -> Optional['replica_managers.ReplicaInfo']:
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


def get_replica_infos(
        service_name: str) -> List['replica_managers.ReplicaInfo']:
    """Gets all replica infos of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            SELECT replica_info FROM replicas
            WHERE service_name=(?)""", (service_name,)).fetchall()
    return [pickle.loads(row[0]) for row in rows]


def total_number_provisioning_replicas() -> int:
    """Returns the total number of provisioning replicas."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute('SELECT replica_info FROM replicas').fetchall()
    provisioning_count = 0
    for row in rows:
        replica_info: 'replica_managers.ReplicaInfo' = pickle.loads(row[0])
        if replica_info.status == ReplicaStatus.PROVISIONING:
            provisioning_count += 1
    return provisioning_count


# === Version functions ===
def add_version(service_name: str) -> int:
    """Adds a version to the database."""

    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT INTO version_specs
            (version, service_name, spec)
            VALUES (
                (SELECT COALESCE(MAX(version), 0) + 1 FROM
                version_specs WHERE service_name = ?), ?, ?)
            RETURNING version""",
            (service_name, service_name, pickle.dumps(None)))

        inserted_version = cursor.fetchone()[0]

    return inserted_version


def add_or_update_version(service_name: str, version: int,
                          spec: 'service_spec.SkyServiceSpec') -> None:
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
        INSERT or REPLACE INTO version_specs
        (service_name, version, spec)
        VALUES (?, ?, ?)""", (service_name, version, pickle.dumps(spec)))


def remove_service_versions(service_name: str) -> None:
    """Removes a replica from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            DELETE FROM version_specs
            WHERE service_name=(?)""", (service_name,))


def get_spec(service_name: str,
             version: int) -> Optional['service_spec.SkyServiceSpec']:
    """Gets spec from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute(
            """\
            SELECT spec FROM version_specs
            WHERE service_name=(?)
            AND version=(?)""", (service_name, version)).fetchall()
    for row in rows:
        return pickle.loads(row[0])
    return None


def delete_version(service_name: str, version: int) -> None:
    """Deletes a version from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            DELETE FROM version_specs
            WHERE service_name=(?)
            AND version=(?)""", (service_name, version))


def delete_all_versions(service_name: str) -> None:
    """Deletes all versions from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            DELETE FROM version_specs
            WHERE service_name=(?)""", (service_name,))
