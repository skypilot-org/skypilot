"""SQLAlchemy schema/DB manager for SkyServe (server-only).

Imported by ``sky.serve.serve_state`` when SQLAlchemy is available. Tables
stay at column 0 so schema edits do not re-indent the parent module.
"""
# pylint: disable=line-too-long,unused-import
from __future__ import annotations

import json

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky.serve import constants
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils
from sky.utils.db import sqlalchemy_deps

sqlalchemy_deps.require_sqlalchemy()

Base = declarative.declarative_base()

# === Database schema ===
services_table = sqlalchemy.Table(
    'services',
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('controller_job_id',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('controller_port',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('load_balancer_port',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('uptime', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('policy', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('auto_restart', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('requested_resources',
                      sqlalchemy.LargeBinary,
                      server_default=None),
    sqlalchemy.Column('requested_resources_str', sqlalchemy.Text),
    sqlalchemy.Column('current_version',
                      sqlalchemy.Integer,
                      server_default=str(constants.INITIAL_VERSION)),
    sqlalchemy.Column('active_versions',
                      sqlalchemy.Text,
                      server_default=json.dumps([])),
    sqlalchemy.Column('load_balancing_policy',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('tls_encrypted', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('pool', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('controller_pid', sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
    # Pod IP where the controller process is running.
    # Written by the sky.serve.service process at startup.
    sqlalchemy.Column('controller_ip', sqlalchemy.Text, server_default=None),
    # User hash of the user that created the service/pool. Passed in from the
    # API server, since the controller itself may be shared among users.
    sqlalchemy.Column('user_hash', sqlalchemy.Text, server_default=None),
    # Blob holding the uploaded workdir/file mounts that the service's task
    # still points at (consolidation mode without a cloud bucket). Each replica
    # launch passes it on so the API server re-extracts the blob on whichever
    # host handles the launch. It also keeps the blob from being GC'd while
    # the service exists.
    sqlalchemy.Column('file_mounts_blob_id',
                      sqlalchemy.Text,
                      server_default=None),
)

replicas_table = sqlalchemy.Table(
    'replicas',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('replica_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('replica_info', sqlalchemy.LargeBinary),
)

version_specs_table = sqlalchemy.Table(
    'version_specs',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('version', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('spec', sqlalchemy.LargeBinary),
    sqlalchemy.Column('yaml_content', sqlalchemy.Text, server_default=None),
)

serve_ha_recovery_script_table = sqlalchemy.Table(
    'serve_ha_recovery_script',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('script', sqlalchemy.Text),
)


def create_table(engine: sqlalchemy.engine.Engine):
    """Creates the service and replica tables if they do not exist."""

    # Enable WAL mode to avoid locking issues.
    # See: issue #3863, #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(engine, migration_utils.SERVE_DB_NAME,
                                         migration_utils.SERVE_VERSION)


_db_manager = db_utils.DatabaseManager('serve/services', create_table)

# Names re-exported into sky.serve.serve_state under sqlalchemy_available().
# Keep this list in sync when adding tables/helpers the facade must expose.
__all__ = [
    'sqlalchemy',
    'sqlalchemy_exc',
    'orm',
    'postgresql',
    'sqlite',
    'declarative',
    'Base',
    'services_table',
    'replicas_table',
    'version_specs_table',
    'serve_ha_recovery_script_table',
    'create_table',
    '_db_manager',
]
