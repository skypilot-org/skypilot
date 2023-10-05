"""The database for services information."""
import pathlib
import sqlite3
from typing import Any, Dict, List, Optional

from sky import status_lib
from sky.serve import constants
from sky.utils import db_utils

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
_CONN.commit()


def add_service(job_id: int, service_name: str, controller_port: int) -> None:
    """Adds a service to the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT INTO services
            (name, controller_job_id, controller_port, status)
            VALUES (?, ?, ?, ?)""",
            (service_name, job_id, controller_port,
             status_lib.ServiceStatus.CONTROLLER_INIT.value))


def remove_service(service_name: str) -> None:
    """Removes a service from the database."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute("""\
            DELETE FROM services WHERE name=(?)""", (service_name,))


def set_uptime(service_name: str, uptime: int) -> None:
    """Sets the uptime of a service."""
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            UPDATE services SET
            uptime=(?) WHERE name=(?)""", (uptime, service_name))


def set_status(service_name: str, status: status_lib.ServiceStatus) -> None:
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
        'status': status_lib.ServiceStatus[status],
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
