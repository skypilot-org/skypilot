"""API server liveness tracking.

Detects and prevents multiple API servers from using the same database
simultaneously, which can cause data corruption, lock contention, and
managed job failures.

Each API server registers itself in the database on startup and
periodically updates a heartbeat timestamp. On startup, if another
server with a recent heartbeat is detected, the server refuses to start
(unless this is a Kubernetes rolling update).
"""
import asyncio
import atexit
import datetime
import os
import platform
import time
import uuid

import sqlalchemy
from sqlalchemy import orm

from sky import global_user_state
from sky import sky_logging
from sky.server import constants as server_constants
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Unique ID for this server instance, set during registration.
_SERVER_ID: str = ''


def _get_server_id() -> str:
    """Get or generate the unique server ID for this instance.

    In Kubernetes, the pod UID is used for stable identification across
    container restarts. Otherwise a random UUID is generated.
    """
    pod_uid = os.environ.get('SKYPILOT_APISERVER_UUID')
    if pod_uid:
        return pod_uid
    return str(uuid.uuid4())


def _is_rolling_update() -> bool:
    """Check if this server is part of a Kubernetes rolling update."""
    return os.environ.get(constants.SKYPILOT_ROLLING_UPDATE_ENABLED,
                          '').lower() == 'true'


def _cleanup_stale_entries(engine: sqlalchemy.engine.Engine) -> None:
    """Remove server entries whose heartbeat has gone stale."""
    threshold = time.time(
    ) - server_constants.SERVER_ALIVE_STALE_THRESHOLD_SECONDS
    table = global_user_state.api_server_instance_table
    with orm.Session(engine) as session:
        session.execute(
            table.delete().where(table.c.last_heartbeat < threshold))
        session.commit()


def _format_server_rows(rows) -> str:
    """Format active server rows for log/error messages."""
    lines = []
    for row in rows:
        started = datetime.datetime.fromtimestamp(
            row.started_at).strftime('%Y-%m-%d %H:%M:%S')
        heartbeat = datetime.datetime.fromtimestamp(
            row.last_heartbeat).strftime('%Y-%m-%d %H:%M:%S')
        lines.append(f'  server_id={row.server_id}  pid={row.pid}  '
                     f'hostname={row.hostname}  started={started}  '
                     f'last_heartbeat={heartbeat}')
    return '\n'.join(lines)


def check_and_register_server() -> None:
    """Check for conflicting servers and register this instance.

    Called once during API server startup, after database initialisation.

    Raises:
        RuntimeError: If another active API server is already using
            the same database and this is *not* a rolling update.
    """
    global _SERVER_ID

    engine = global_user_state.initialize_and_get_db()

    # 1. Remove entries that have not heartbeated recently.
    _cleanup_stale_entries(engine)

    # 2. Check for remaining active servers.
    table = global_user_state.api_server_instance_table
    with orm.Session(engine) as session:
        active_rows = session.execute(table.select()).fetchall()

    if active_rows:
        servers_str = _format_server_rows(active_rows)
        if _is_rolling_update():
            logger.warning(
                'Rolling update: other API server(s) still '
                f'active:\n{servers_str}\n'
                'This is expected during a Kubernetes rolling update.')
        else:
            raise RuntimeError(
                'Another SkyPilot API server is already using this '
                'database. Running multiple API servers against the same '
                'database causes data corruption, lock contention, and '
                'managed-job failures.\n'
                f'\nActive server(s):\n{servers_str}\n'
                '\nIf the other server has been shut down or has crashed, '
                'wait up to '
                f'{server_constants.SERVER_ALIVE_STALE_THRESHOLD_SECONDS} '
                'seconds for its heartbeat to expire and try again.\n'
                '\nIf you are intentionally running multiple servers '
                '(e.g. during a Kubernetes rolling update), set '
                f'{constants.SKYPILOT_ROLLING_UPDATE_ENABLED}=true.')

    # 3. Register this server.
    _SERVER_ID = _get_server_id()
    now = time.time()
    with orm.Session(engine) as session:
        # Upsert: a pod may restart with the same UID.
        session.execute(table.delete().where(table.c.server_id == _SERVER_ID))
        session.execute(table.insert().values(
            server_id=_SERVER_ID,
            pid=os.getpid(),
            hostname=platform.node(),
            started_at=now,
            last_heartbeat=now,
        ))
        session.commit()

    logger.info(f'Registered API server instance: {_SERVER_ID}')


def _deregister_server() -> None:
    """Best-effort removal of this server's entry on shutdown."""
    if not _SERVER_ID:
        return
    try:
        engine = global_user_state.initialize_and_get_db()
        table = global_user_state.api_server_instance_table
        with orm.Session(engine) as session:
            session.execute(
                table.delete().where(table.c.server_id == _SERVER_ID))
            session.commit()
        logger.info(f'Deregistered API server instance: {_SERVER_ID}')
    except Exception:  # pylint: disable=broad-except
        # If the DB is unreachable the entry will simply go stale.
        logger.debug(f'Failed to deregister server {_SERVER_ID}', exc_info=True)


atexit.register(_deregister_server)


async def heartbeat_daemon() -> None:
    """Background coroutine that keeps this server's heartbeat fresh.

    Also periodically cleans up stale entries from other servers.
    Runs on the background event loop alongside other global daemons.
    """
    engine = global_user_state.initialize_and_get_db()
    table = global_user_state.api_server_instance_table

    while True:
        try:
            await asyncio.sleep(
                server_constants.SERVER_ALIVE_HEARTBEAT_INTERVAL_SECONDS)
            now = time.time()
            with orm.Session(engine) as session:
                session.execute(table.update().where(
                    table.c.server_id == _SERVER_ID).values(last_heartbeat=now))
                session.commit()
            # Housekeeping: remove stale entries from crashed servers.
            _cleanup_stale_entries(engine)
        except asyncio.CancelledError:
            logger.info('Heartbeat daemon cancelled')
            break
        except Exception:  # pylint: disable=broad-except
            logger.debug('Error updating server heartbeat', exc_info=True)
