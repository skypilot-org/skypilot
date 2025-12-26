#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "sqlalchemy[asyncio]",
#   "asyncpg",
#   "aiosqlite",
#   "fastapi",
#   "uvicorn",
# ]
# ///
"""
SQLite to PostgreSQL Migration Script using SQLAlchemy
"""
#   "psycopg2-binary",

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import ColumnCollection
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Index
from sqlalchemy import inspect
from sqlalchemy import Integer
from sqlalchemy import LargeBinary
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import Time
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import insert as pg_insert
# from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql import text

# import uvicorn

# sky api port to reuse liveness checks
PORT = 46580

app_name = "sky-db-migration"

BATCH_SIZE = os.environ.get("BATCH_SIZE", 1000)
INCLUDE_INDEXES = os.environ.get("INCLUDE_INDEXES", "true").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(app_name)
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))


class SQLiteToPostgreSQLMigrator:
    # SQLAlchemy type mapping from SQLite to PostgreSQL
    TYPE_MAPPING = {
        # SQLite types to SQLAlchemy PostgreSQL types
        'INTEGER': postgresql.INTEGER,
        'TEXT': postgresql.TEXT,
        'REAL': postgresql.REAL,
        'BLOB': postgresql.BYTEA,
        'NUMERIC': postgresql.NUMERIC,
        'VARCHAR': postgresql.VARCHAR,
        'CHAR': postgresql.CHAR,
        'DATE': postgresql.DATE,
        'DATETIME': postgresql.TIMESTAMP,
        'TIMESTAMP': postgresql.TIMESTAMP,
        'TIME': postgresql.TIME,
        'BOOLEAN': postgresql.BOOLEAN,
        'FLOAT': postgresql.FLOAT,
        'DOUBLE': postgresql.DOUBLE_PRECISION,
        # Handle some common SQLite affinity types
        'INT': postgresql.INTEGER,
        'TINYINT': postgresql.SMALLINT,
        'SMALLINT': postgresql.SMALLINT,
        'BIGINT': postgresql.BIGINT,
        'DECIMAL': postgresql.NUMERIC,
    }

    def __init__(self,
                 sqlite_url: str,
                 postgres_url: str,
                 dry_run: bool = False):
        self.sqlite_url = sqlite_url
        self.postgres_url = postgres_url
        self.sqlite_engine = None
        self.postgres_engine = None
        self.sqlite_metadata = None
        self.postgres_metadata = None
        self.sqlite_session_maker = None
        self.postgres_session_maker = None

        self.dry_run = dry_run

        # variables to check status
        self.status = 'created'
        self.table_being_migrated = None
        self.data_migration_offset = 0
        self.data_migrated_rows = 0

    async def connect_databases(self):
        """Create database engines and metadata objects"""
        try:
            # Create SQLite engine
            self.sqlite_engine = create_async_engine(self.sqlite_url,
                                                     echo=False,
                                                     pool_pre_ping=True)

            # Create PostgreSQL engine
            self.postgres_engine = create_async_engine(
                self.postgres_url,
                echo=False,
                pool_pre_ping=True,
                # client_encoding='utf8'
            )

            # Test connections
            async with self.sqlite_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(f"Connected to SQLite database: {self.sqlite_url}")

            async with self.postgres_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(f"Connected to PostgreSQL database")

            # Create metadata objects
            self.sqlite_metadata = MetaData()
            self.postgres_metadata = MetaData()

            self.sqlite_session_maker = async_sessionmaker(
                bind=self.sqlite_engine)
            self.postgres_session_maker = async_sessionmaker(
                bind=self.postgres_engine)

            self.status = 'connected'

        except Exception as e:
            logger.error(f"Failed to connect to databases: {e}")
            raise

    async def dispose_engines(self) -> None:
        """Dispose of database engines"""
        if self.sqlite_engine:
            await self.sqlite_engine.dispose()
        if self.postgres_engine:
            await self.postgres_engine.dispose()
        logger.info("Database engines disposed")

    async def reflect_sqlite_schema(self) -> List[str]:
        """Reflect SQLite schema and return table names"""
        try:
            async with self.sqlite_engine.begin() as conn:
                columns_info = await conn.run_sync(
                    lambda sync_conn: self.sqlite_metadata.reflect(bind=
                                                                   sync_conn))

            table_names = list(self.sqlite_metadata.tables.keys())
            logger.info(f"Found {len(table_names)} tables: {table_names}")
            return table_names
        except Exception as e:
            logger.error(f"Failed to reflect SQLite schema: {e}")
            raise

    def convert_column_type(self, column_info: Dict[str, Any]) -> Any:
        """Convert SQLite column type to PostgreSQL SQLAlchemy type"""
        sqlite_type = str(column_info['type']).upper()

        # Handle parameterized types (e.g., VARCHAR(255), DECIMAL(10,2))
        base_type = sqlite_type.split('(')[0].strip()

        # Check for direct mapping
        if base_type in self.TYPE_MAPPING:
            pg_type = self.TYPE_MAPPING[base_type]

            # Handle types with parameters
            if '(' in sqlite_type and hasattr(pg_type, '__call__'):
                # Extract parameters
                params_str = sqlite_type[sqlite_type.find('(') +
                                         1:sqlite_type.find(')')]
                try:
                    if ',' in params_str:
                        # Handle types like DECIMAL(10,2)
                        precision, scale = map(int, params_str.split(','))
                        if base_type in ['NUMERIC', 'DECIMAL']:
                            return pg_type(precision, scale)
                        elif base_type in ['VARCHAR', 'CHAR']:
                            return pg_type(precision)
                    else:
                        # Handle types like VARCHAR(255)
                        length = int(params_str)
                        return pg_type(length)
                except (ValueError, TypeError):
                    logger.warning(
                        f"Could not parse parameters for type {sqlite_type}")

            return pg_type()

        # Handle some special cases
        if 'INT' in base_type:
            return postgresql.INTEGER()
        elif any(text_type in base_type
                 for text_type in ['TEXT', 'CLOB', 'STRING']):
            return postgresql.TEXT()
        elif any(num_type in base_type
                 for num_type in ['REAL', 'FLOAT', 'DOUBLE']):
            return postgresql.REAL()
        elif 'BLOB' in base_type:
            return postgresql.BYTEA()

        # Default to TEXT
        logger.warning(f"Unknown type '{sqlite_type}', defaulting to TEXT")
        return postgresql.TEXT()

    async def create_postgres_table(self, sqlite_table: Table) -> Table:
        """Create PostgreSQL table from SQLite table definition"""
        table_name = sqlite_table.name

        # Get detailed column information using inspect
        async with self.sqlite_engine.begin() as conn:
            columns_info = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns(table_name))
            pk_constraint = await conn.run_sync(lambda sync_conn: inspect(
                sync_conn).get_pk_constraint(table_name))

        postgres_columns = []

        for col_info in columns_info:
            col_name = col_info['name']

            # ignore typo column from skypilot bug
            if col_name == "user_hasha":
                continue

            # Convert column type
            pg_type = self.convert_column_type(col_info)

            # Create column with properties
            column_kwargs = {
                'nullable': col_info.get('nullable', True),
                'primary_key': col_name in pk_constraint.get(
                    'constrained_columns', [])
            }

            # Handle default values
            if col_info.get('default') is not None:
                default = col_info['default']
                if default.upper() == 'NULL':
                    default = None
                # Clean up SQLite-specific defaults
                if isinstance(default, str):
                    if default.upper() in ('CURRENT_TIMESTAMP', 'CURRENT_DATE',
                                           'CURRENT_TIME'):
                        column_kwargs['server_default'] = text(default)
                    elif default.startswith("'") and default.endswith("'"):
                        column_kwargs['server_default'] = default[
                            1:-1]  # Remove quotes
                    else:
                        try:
                            # Try to parse as literal value
                            column_kwargs['default'] = default
                        except:
                            column_kwargs['server_default'] = text(default)
                else:
                    column_kwargs['server_default'] = default

            postgres_columns.append(Column(col_name, pg_type, **column_kwargs))

        # Create the PostgreSQL table
        postgres_table = Table(
            table_name,
            self.postgres_metadata,
            *postgres_columns,
            extend_existing=True,
        )
        logger.debug(
            f"pg table from {sqlite_table.name}: {postgres_table.columns}")

        return postgres_table

    async def create_postgres_tables(self, tables_already_created=set()):
        pg_table_names = [t.key for t in self.postgres_metadata.sorted_tables]

        if self.dry_run:
            logger.info(f"Would've made tables in postgres: {pg_table_names}")
            return

        async with self.postgres_engine.begin() as postgres_conn:
            for table_name in pg_table_names:
                table_present = await postgres_conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).has_table(table_name))
                if table_present:
                    tables_already_created |= {table_name}

        missing_table_names = set(pg_table_names) - tables_already_created
        missing_tables = [
            self.postgres_metadata.tables[table_name]
            for table_name in missing_table_names
        ]

        async with self.postgres_engine.begin() as postgres_conn:
            await postgres_conn.run_sync(
                lambda sync_conn: self.postgres_metadata.create_all(
                    bind=sync_conn, tables=missing_tables, checkfirst=False))
            logger.info(
                f"Attempted to create postgres tables: {missing_table_names}")

        async with self.postgres_engine.begin() as postgres_conn:
            for table_name in pg_table_names:
                table_present = await postgres_conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).has_table(table_name))
                if table_present:
                    tables_already_created |= {table_name}

        if (missing_table_names :=
                set(pg_table_names) - tables_already_created):
            logger.debug(
                f"tables already created: {tables_already_created}, tables wanted: {pg_table_names}, waiting for tables {missing_table_names} to be created..."
            )
            await asyncio.sleep(2)
            # maybe infinite loop idk
            await self.create_postgres_tables(tables_already_created)

    async def create_postgres_indexes(self, sqlite_table: Table,
                                      postgres_table: Table):
        """Create indexes for PostgreSQL table"""
        async with self.sqlite_engine.begin() as conn:
            indexes = await conn.run_sync(lambda sync_conn: inspect(sync_conn).
                                          get_indexes(sqlite_table.name))

        self.status = f'creating indexes for {sqlite_table.name}'

        for index_info in indexes:
            index_name = f"{postgres_table.name}_{index_info['name']}_idx"
            column_names = index_info['column_names']
            unique = index_info.get('unique', False)

            # Skip primary key indexes (handled by table definition)
            if any('autoindex' in index_info['name'].lower() for _ in [None]):
                continue

            try:
                # Get column objects
                index_columns = [
                    postgres_table.c[col_name] for col_name in column_names
                ]

                # Create index
                index = Index(index_name, *index_columns, unique=unique)

                if not self.dry_run:
                    async with self.postgres_engine.begin() as conn:
                        await conn.run_sync(
                            lambda sync_conn: index.create(sync_conn))

                logger.info(f"Created index: {index_name}")

            except Exception as e:
                logger.warning(f"Failed to create index {index_name}: {e}")

    def serialize_value(self, value: Any) -> Any:
        """Serialize value for database insertion"""
        if value is None:
            return None
        elif isinstance(value, (datetime, date, time)):
            return value
        elif isinstance(value, Decimal):
            return value
        elif isinstance(value, bytes):
            return value
        elif isinstance(value, (dict, list)):
            return json.dumps(value)
        else:
            return value

    async def _migrate_table_data_loop(self,
                                       sqlite_table: Table,
                                       postgres_table: Table,
                                       batch_size: int = 1000,
                                       empty_table=True):
        table_name = sqlite_table.name
        column_names = [col.name for col in postgres_table.columns]

        pk_constraint = postgres_table.primary_key
        if getattr(pk_constraint, 'columns'):
            pk_constraint = list(pk_constraint)
        else:
            logger.info(f'pk constraint of {table_name}: {pk_constraint}')

        async with self.sqlite_session_maker(
        ) as sqlite_session, self.postgres_session_maker() as postgres_session:
            try:
                # Fetch batch from SQLite
                select_query = sqlite_table.select().add_columns(
                    *(sqlite_table.c[col_name] for col_name in column_names
                     )).limit(batch_size).offset(self.data_migration_offset)

                result = await sqlite_session.execute(select_query)
                rows = result.fetchall()

                if not rows:
                    return

                # Convert rows to dictionaries
                column_names = [col.name for col in postgres_table.columns]
                logger.debug(f"column names for {table_name}: {column_names}")
                data_dicts = []

                for row in rows:
                    row_dict = {
                        col_name: self.serialize_value(getattr(row, col_name))
                        for col_name in column_names
                    }
                    logger.debug(
                        f"row to insert into {table_name}:\n{row_dict}")
                    data_dicts.append(row_dict)

                # Bulk insert into PostgreSQL
                if data_dicts and not self.dry_run:
                    insert_stmt = pg_insert(postgres_table).values(data_dicts)
                    if not empty_table:
                        insert_stmt = insert_stmt.on_conflict_do_update(
                            index_elements=pk_constraint,
                            set_={
                                c.key: c
                                for c in postgres_table.c
                                if c.key not in pk_constraint
                            })
                    await postgres_session.execute(insert_stmt)
                    await postgres_session.commit()
                elif data_dicts and self.dry_run:
                    logger.info(
                        f"would've inserted {len(data_dicts)} rows into {sqlite_table.name}"
                    )

                self.data_migrated_rows += len(rows)
                self.data_migration_offset += batch_size

                logger.info(
                    f"Migrated {self.data_migrated_rows}/{self.data_migration_total_rows} rows for table {sqlite_table.name}"
                )

            except Exception as e:
                await postgres_session.rollback()
                logger.error(
                    f"Failed to migrate batch for table {sqlite_table.name}: {e}"
                )
                raise Exception(
                    f"Failed to migrate batch for table {sqlite_table.name}"
                ) from e

    async def migrate_table_data(self,
                                 sqlite_table: Table,
                                 postgres_table: Table,
                                 batch_size: int = 1000):
        """Migrate data from SQLite table to PostgreSQL table"""
        try:
            # Count total rows
            async with self.sqlite_engine.connect() as sqlite_conn:
                count_result = await sqlite_conn.execute(
                    text(f'SELECT COUNT(*) FROM "{sqlite_table.name}"'))
                total_rows = count_result.scalar()

            if total_rows == 0:
                logger.info(
                    f"Table {sqlite_table.name} is empty, skipping data migration"
                )
                return

            logger.info(
                f"Migrating {total_rows} rows from table: {sqlite_table.name}")

            self.table_being_migrated = sqlite_table.name
            self.data_migration_offset = 0
            self.data_migrated_rows = 0
            self.data_migration_total_rows = total_rows
            self.status = f'migrating data in table: {sqlite_table.name}'

            # check if any data is already in postgres
            async with self.postgres_engine.begin() as postgres_conn:
                while True:
                    table_present = await postgres_conn.run_sync(
                        lambda sync_conn: inspect(sync_conn).has_table(
                            postgres_table.key))
                    if table_present:
                        break
                    logger.debug(
                        f"waiting for table {postgres_table.key} to be created..."
                    )
                    await asyncio.sleep(2)

                count_result = await postgres_conn.execute(
                    text(f'SELECT COUNT(*) FROM "{postgres_table.key}"'))
                prior_rows = count_result.scalar()

            while self.data_migration_offset < self.data_migration_total_rows:
                await self._migrate_table_data_loop(sqlite_table,
                                                    postgres_table,
                                                    batch_size,
                                                    empty_table=prior_rows == 0)

            logger.info(
                f"Successfully migrated all {self.data_migrated_rows} rows for table {sqlite_table.name}"
            )

        except Exception as e:
            logger.error(
                f"Data migration failed for table {sqlite_table.name}: {e}")
            raise Exception(
                f"Data migration failed for table {sqlite_table.name}") from e

    # async def async_migrate(
    #     self, batch_size: int = 1000, include_indexes: bool = True,
    #             tables_to_migrate: Optional[List[str]] = None):
    #     self.migrate(batch_size=batch_size, include_indexes=include_indexes, tables_to_migrate=tables_to_migrate)

    # def migrate(
    async def migrate(self,
                      batch_size: int = 1000,
                      include_indexes: bool = True,
                      tables_to_migrate: Optional[List[str]] = None):
        """Perform complete migration"""
        try:
            await self.connect_databases()
        except Exception as e:
            logger.error(f"Failed to connect to databases: {e}")
            raise

        try:
            self.status = 'starting'
            # Reflect SQLite schema
            table_names = await self.reflect_sqlite_schema()

            # Filter tables if specified
            if tables_to_migrate:
                table_names = [t for t in table_names if t in tables_to_migrate]
                logger.info(f"Migrating only specified tables: {table_names}")

            # Create all PostgreSQL tables first
            self.status = 'creating tables'
            postgres_tables = {}
            for table_name in table_names:
                logger.info(f"Creating schema for table: {table_name}")
                sqlite_table = self.sqlite_metadata.tables[table_name]
                postgres_table = await self.create_postgres_table(sqlite_table)
                postgres_tables[table_name] = postgres_table

            # Create all tables in PostgreSQL
            logger.info("Creating tables in PostgreSQL...")
            await self.create_postgres_tables()

            # Migrate data for each table
            self.status = 'starting data copy'
            for table_name in table_names:
                logger.info(f"Migrating data for table: {table_name}")
                sqlite_table = self.sqlite_metadata.tables[table_name]
                postgres_table = postgres_tables[table_name]

                # Migrate data
                await self.migrate_table_data(sqlite_table, postgres_table,
                                              batch_size)

                # Create indexes if requested
                if include_indexes:
                    logger.info(f"Creating indexes for table: {table_name}")
                    await self.create_postgres_indexes(sqlite_table,
                                                       postgres_table)

            self.status = 'done'
            logger.info("Migration completed successfully!")

        except Exception as e:
            logger.error(f"Migration failed: {traceback.format_exc()}")
            self.status = 'failed'
            raise Exception("Migration failure") from e

        finally:
            await self.dispose_engines()
            if self.status != 'failed':
                self.status = 'cleaned'

    def get_status(self):
        status = f'{self.status}'
        if self.table_being_migrated:
            status += f' - {self.table_being_migrated}: {self.data_migration_offset}/{self.data_migration_total_rows}'
        return status


class SkyDbMigrator:

    def __init__(self):
        postgres_uri = os.environ.get('FUTURE_SKYPILOT_DB_CONNECTION_URI')
        if not postgres_uri:
            logger.error(
                "Set FUTURE_SKYPILOT_DB_CONNECTION_URI env var to postgres db")
            sys.exit(1)

        # use a file to mark the migration is ongoing
        self.migrate_lock_file = Path.home() / 'sky_db_migration.lock'
        if self.migrate_lock_file.exists():
            # should only be one migrator instance ever
            logger.error(f"Migration is already in progress")
            sys.exit(1)

        if not postgres_uri.startswith('postgresql+asyncpg'):
            postgres_uri = postgres_uri.replace('postgresql',
                                                'postgresql+asyncpg', 1)

        self.postgres_uri = postgres_uri

        # maybe only migrate state?
        sky_db_names = [
            'state',
            'spot_jobs',
            'serve/services',
        ]
        self.sky_dbs = {
            db_name: Path.home() / f'.sky/{db_name}.db'
            for db_name in sky_db_names
        }

        self.migrators = {}
        self.migration_tasks = {}

        for db_name, db_path in self.sky_dbs.items():
            if not db_path.exists():
                logger.error(f"DB file not found: {db_path}")
                continue

            sqlite_url = f'sqlite+aiosqlite:///{db_path}'
            migrator = SQLiteToPostgreSQLMigrator(sqlite_url,
                                                  postgres_uri,
                                                  dry_run=DRY_RUN)

            self.migrators[db_name] = migrator

        self.status = 'ready'

    async def start_migration(self) -> None:
        logger.info("Starting migration, locking...")
        self.migrate_lock_file.touch()
        self.status = 'running'
        for db_name, migrator in self.migrators.items():
            self.migration_tasks[db_name] = asyncio.create_task(
                self.migrators[db_name].migrate(
                    batch_size=
                    BATCH_SIZE,  # Adjust batch size based on your needs
                    include_indexes=
                    INCLUDE_INDEXES,  # Set to False to skip index creation
                    tables_to_migrate=
                    None,  # Specify list of tables or None for all
                ),
                name=f"migrate_{db_name}")

    def backup_config(self):
        global DRY_RUN
        if DRY_RUN:
            return
        sky_config_path = Path.home() / '.sky/config.yaml'
        config_bak = sky_config_path.with_suffix('.yaml.bak')
        if sky_config_path.exists() and not config_bak.exists():
            sky_config_path.rename(config_bak)

    async def check_status(self) -> tuple[bool, bool, str]:
        statuses = []
        for db_name, task in self.migration_tasks.items():
            if task.done():
                if task.exception():
                    statuses.append(
                        f"✗ {db_name} - Migration failed: {task.exception()}")
                else:
                    statuses.append(f"✓ {db_name} - Migration completed")
            else:
                statuses.append(f"⚠ {db_name} - Migration in progress:\n\t" +
                                self.migrators[db_name].get_status())
        all_done = all(task.done() for task in self.migration_tasks.values())
        all_success = all(task.done() and not task.exception() and
                          self.migrators[db_name].status != 'failed'
                          for db_name, task in self.migration_tasks.items())
        if all_success:
            self.migrate_lock_file.unlink()
            self.backup_config()
            self.status = 'done'
        elif all_done and not all_success:
            self.status = 'failed'
        status_message = f'Sky Migration: {self.status}\n\t' + '\n\t'.join(
            statuses)
        return (all_done, all_success, status_message)


# global sky migrator
sky_migrator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global sky_migrator
    sky_migrator = SkyDbMigrator()
    await sky_migrator.start_migration()
    logger.info("Started migrations")
    yield
    logger.info("shutting down")


app = FastAPI(lifespan=lifespan)


@app.route('/api/health', methods=['GET', 'HEAD'])
@app.route('/', methods=['GET', 'HEAD'])
async def health_check(request):
    global sky_migrator
    try:
        if sky_migrator is None:
            logger.error('Migration not initialized')
            return JSONResponse(
                content={'status': 'Sky DB Migration not initialized'},
                status_code=503)
        done, success, status = await sky_migrator.check_status()
        if not done:
            logger.info(f'Migration in progress:\n{status}')
            return JSONResponse(content={'status': 'OK'}, status_code=200)
        elif done and not success:
            logger.error(f'Migration failed:\n{status}')
            return JSONResponse(content={'status': 'Migration failed'},
                                status_code=501)
        elif done and success:
            logger.info(f'Migration completed:\n{status}')
            return JSONResponse(content={'status': 'OK'}, status_code=200)
        else:
            logger.error(f'Unknown migration status:\n{status}')
            return JSONResponse(content={'status': 'Unknown migration status'},
                                status_code=500)
    except Exception as e:
        logger.error(f'Health check endpoint error:{e}')
        return JSONResponse(content={'status': 'Internal Server Error'},
                            status_code=503)


async def main():
    import uvicorn

    global DRY_RUN
    parser = argparse.ArgumentParser(description="Sky DB Migration")
    parser.add_argument("-d",
                        "--dry-run",
                        action="store_true",
                        default=DRY_RUN,
                        help="Whether to dry run the migration")

    args = parser.parse_args()

    DRY_RUN = args.dry_run

    logger.info("Stating sky db migration...")
    try:
        uvicorn_app = 'sky_sql_migrate:app'
        config = uvicorn.Config(
            uvicorn_app,
            host='0.0.0.0',
            port=PORT,
            log_level=logger.level,
            workers=MAX_WORKERS,
        )
        server = uvicorn.Server(config)
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
